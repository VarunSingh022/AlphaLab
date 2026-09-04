"""The Deployment Manager state model and release-registration operations.

Like ``alphalab.model_registry.registry``, every type the manager works with
lives in this one module so the deployment and rollback modules depend on it
without cycles.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from alphalab.common.registry import with_mapping_item
from alphalab.deployment_manager.exceptions import DeploymentManagerInputError
from alphalab.deployment_manager.packaging import ReleasePackage, build_release


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
        deployments: Every deployment/rollback, in the order it happened.
            Rollback reads this to find the previous active release per
            environment.
    """

    releases: Mapping[str, tuple[ReleasePackage, ...]] = field(default_factory=dict)
    deployments: tuple[DeploymentRecord, ...] = ()


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
    existing = manager.releases.get(name, ())
    package = build_release(name, len(existing) + 1, components, config, timestamp)
    new_releases = with_mapping_item(manager.releases, name, (*existing, package))
    return replace(manager, releases=new_releases), package


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
    return releases


def get_release(manager: DeploymentManager, name: str, version: int) -> ReleasePackage:
    """Returns a specific release version.

    Raises:
        DeploymentManagerInputError: If ``name`` or that ``version`` is unknown.
    """
    for candidate in list_releases(manager, name):
        if candidate.version == version:
            return candidate
    raise DeploymentManagerInputError(f"Release '{name}' has no version {version}.")


def latest_release(manager: DeploymentManager, name: str) -> ReleasePackage:
    """Returns the highest-numbered version of release line ``name``.

    Raises:
        DeploymentManagerInputError: If ``name`` has no registered releases.
    """
    return list_releases(manager, name)[-1]
