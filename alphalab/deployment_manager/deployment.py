"""Deploying registered releases to environments.

Each environment has at most one active release at a time -- the one named by
its most recent :class:`DeploymentRecord`. Deploying is append-only: the ledger
never loses the record of what was live before.
"""

from dataclasses import replace

from alphalab.common.append_log import AppendOnlyLog
from alphalab.deployment_manager.exceptions import DeploymentManagerInputError
from alphalab.deployment_manager.packaging import ReleasePackage, verify_checksum
from alphalab.deployment_manager.releases import (
    DeploymentManager,
    DeploymentRecord,
    get_release,
)


def record_deployment(
    manager: DeploymentManager,
    environment: str,
    release: ReleasePackage,
    is_rollback: bool,
    timestamp: float,
) -> DeploymentManager:
    """Append one deployment to the ledger and to its environment's index."""

    incumbent = active_release(manager, environment)
    record = DeploymentRecord(
        environment=environment,
        release_name=release.name,
        version=release.version,
        replaced_version=incumbent.version if incumbent is not None else None,
        is_rollback=is_rollback,
        timestamp=timestamp,
    )
    history = manager.environments.get(environment, AppendOnlyLog[DeploymentRecord]())
    return replace(
        manager,
        deployments=manager.deployments.append(record),
        environments=manager.environments.set(environment, history.append(record)),
    )


def deploy(
    manager: DeploymentManager,
    name: str,
    version: int,
    environment: str,
    timestamp: float,
) -> DeploymentManager:
    """Makes ``name`` version ``version`` the active release in ``environment``.

    Raises:
        DeploymentManagerInputError: If ``environment`` is blank, the release
            version is unknown, its stored checksum no longer matches its
            manifest, or it is already the active release in ``environment``.
    """
    if not environment.strip():
        raise DeploymentManagerInputError("environment cannot be empty.")

    release = get_release(manager, name, version)
    if not verify_checksum(release):
        raise DeploymentManagerInputError(
            f"Release '{name}' version {version} fails checksum verification; refusing to deploy."
        )

    incumbent = active_release(manager, environment)
    if incumbent is not None and incumbent.name == name and incumbent.version == version:
        raise DeploymentManagerInputError(
            f"Release '{name}' version {version} is already active in '{environment}'."
        )

    return record_deployment(manager, environment, release, is_rollback=False, timestamp=timestamp)


def active_release(manager: DeploymentManager, environment: str) -> ReleasePackage | None:
    """Returns the release currently active in ``environment``, or ``None``.

    The environment's newest ledger entry, read from the environment index, so
    this is O(1) and safe to call from the deployment path.
    """
    history = manager.environments.get(environment)
    if not history:
        return None
    record = history[-1]
    return get_release(manager, record.release_name, record.version)


def deployed_environments(manager: DeploymentManager) -> tuple[str, ...]:
    """Returns every environment that has ever been deployed to, in first-seen order."""
    return tuple(manager.environments)
