"""The Deployment Manager state model and release-registration operations.

Like ``alphalab.model_registry.registry``, every type the manager works with
lives in this one module so the deployment and rollback modules depend on it
without cycles.

Containers and indexes
----------------------
Every write returns a new immutable manager. At v2.3 registering a release
copied the whole release mapping and rebuilt the whole version tuple, and every
deployment rebuilt the whole ledger, so ``N`` writes cost ``O(N^2)``. Releases
are now an :class:`~alphalab.common.append_log.AppendOnlyLog` per name inside a
:class:`~alphalab.common.persistent_map.PersistentMap`, and the ledger is an
``AppendOnlyLog``; both share structure instead of copying.

``environments`` indexes the same ledger by environment, which is the question
the write path actually asks -- "what is active here, and what was active
before?" Reading the newest and second-newest record of one environment is O(1)
instead of a backwards scan of every deployment ever made.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.deployment_manager.exceptions import DeploymentManagerInputError
from alphalab.deployment_manager.packaging import ReleasePackage, build_release

__all__ = [
    "DeploymentManager",
    "DeploymentRecord",
    "get_release",
    "latest_release",
    "list_releases",
    "register_release",
    "release_names",
]


@dataclass(frozen=True, slots=True)
class DeploymentRecord:
    """An append-only audit entry for a single deployment or rollback.

    Attributes:
        environment: The environment that changed, e.g. ``"staging"``.
        release_name: The deployed release line.
        version: The release version now active in ``environment``.
        replaced_version: The version that was active before, or ``None`` if the
            environment was previously empty.
        is_rollback: Whether this record was produced by
            :func:`alphalab.deployment_manager.rollback.rollback`.
        timestamp: Unix timestamp of the change.
    """

    environment: str
    release_name: str
    version: int
    replaced_version: int | None
    is_rollback: bool
    timestamp: float


@dataclass(frozen=True, slots=True)
class DeploymentManager:
    """Immutable ledger of every registered release and every deployment.

    Attributes:
        releases: Release name -> that line's packages, ascending by version.
            Version ``v`` is entry ``v - 1``, because versions are dense and
            assigned in registration order.
        deployments: Every deployment/rollback, in the order it happened.
        environments: Environment -> that environment's deployments, in order.
            The same records as ``deployments``, indexed by the question the
            deployment and rollback paths ask. Maintained by this package's
            functions, the way :class:`~alphalab.oms.book.OrderBook` maintains
            its indexes -- build a manager through :func:`register_release` and
            :func:`~alphalab.deployment_manager.deployment.deploy` rather than
            by hand.
    """

    releases: PersistentMap[str, AppendOnlyLog[ReleasePackage]] = field(
        default_factory=PersistentMap
    )
    deployments: AppendOnlyLog[DeploymentRecord] = field(default_factory=AppendOnlyLog)
    environments: PersistentMap[str, AppendOnlyLog[DeploymentRecord]] = field(
        default_factory=PersistentMap
    )

    def __post_init__(self) -> None:
        # The container's own type only -- see ModelRegistry.__post_init__ for
        # why inspecting the entries here would reintroduce a quadratic.
        if not isinstance(self.releases, PersistentMap):
            object.__setattr__(self, "releases", _as_lines(self.releases))
        if not isinstance(self.deployments, AppendOnlyLog):
            object.__setattr__(self, "deployments", AppendOnlyLog(self.deployments))


def _as_lines(releases: Mapping[str, Any]) -> PersistentMap[str, AppendOnlyLog[ReleasePackage]]:
    """Accept a hand-built ``name -> packages`` mapping in any ordered shape."""

    def line(value: Any) -> AppendOnlyLog[ReleasePackage]:
        if isinstance(value, AppendOnlyLog):
            return value
        packages: Iterable[ReleasePackage] = value.values() if isinstance(value, Mapping) else value
        return AppendOnlyLog(packages)

    return PersistentMap((name, line(value)) for name, value in releases.items())


def register_release(
    manager: DeploymentManager,
    name: str,
    components: Mapping[str, str],
    config: Mapping[str, str],
    timestamp: float,
) -> tuple[DeploymentManager, ReleasePackage]:
    """Builds and registers the next version of release line ``name``.

    The first registration of a name is version 1; each subsequent one
    increments. The package's checksum is computed at build time.

    Raises:
        DeploymentManagerInputError: Propagated from
            :func:`alphalab.deployment_manager.packaging.build_release` on
            invalid inputs.
    """
    existing: AppendOnlyLog[ReleasePackage] = manager.releases.get(name, AppendOnlyLog())
    package = build_release(name, len(existing) + 1, components, config, timestamp)
    return replace(manager, releases=manager.releases.set(name, existing.append(package))), package


def release_names(manager: DeploymentManager) -> tuple[str, ...]:
    """Returns every registered release name, in registration order."""
    return tuple(manager.releases)


def list_releases(manager: DeploymentManager, name: str) -> tuple[ReleasePackage, ...]:
    """Returns every version of release line ``name``, ascending.

    Raises:
        DeploymentManagerInputError: If ``name`` has no registered releases.
    """
    releases = manager.releases.get(name)
    if not releases:
        raise DeploymentManagerInputError(f"No release registered under name '{name}'.")
    return releases.to_tuple()


def get_release(manager: DeploymentManager, name: str, version: int) -> ReleasePackage:
    """Returns a specific release version.

    Raises:
        DeploymentManagerInputError: If ``name`` or that ``version`` is unknown.
    """
    releases = manager.releases.get(name)
    if not releases:
        raise DeploymentManagerInputError(f"No release registered under name '{name}'.")
    if version < 1 or version > len(releases):
        raise DeploymentManagerInputError(f"Release '{name}' has no version {version}.")
    return releases[version - 1]


def latest_release(manager: DeploymentManager, name: str) -> ReleasePackage:
    """Returns the highest-numbered version of release line ``name``.

    Raises:
        DeploymentManagerInputError: If ``name`` has no registered releases.
    """
    releases = manager.releases.get(name)
    if not releases:
        raise DeploymentManagerInputError(f"No release registered under name '{name}'.")
    return releases[-1]
