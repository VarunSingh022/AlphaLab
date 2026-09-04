"""Rolling an environment back to its previously active release.

Defined entirely in terms of the ``DeploymentManager.deployments`` log: the
release to roll back to is whatever was active in the environment immediately
before the current deployment.
"""

from alphalab.deployment_manager.deployment import active_release, record_deployment
from alphalab.deployment_manager.exceptions import DeploymentManagerInputError
from alphalab.deployment_manager.releases import (
    DeploymentManager,
    DeploymentRecord,
    get_release,
)


def deployment_history(
    manager: DeploymentManager, environment: str | None = None
) -> tuple[DeploymentRecord, ...]:
    """Returns deployment records in order, optionally filtered to one environment."""
    if environment is None:
        return manager.deployments
    return tuple(record for record in manager.deployments if record.environment == environment)


def previous_release(manager: DeploymentManager, environment: str) -> tuple[str, int] | None:
    """Returns the ``(name, version)`` active in ``environment`` before the current one.

    ``None`` if the environment has had fewer than two deployments.
    """
    history = deployment_history(manager, environment)
    if len(history) < 2:
        return None
    prior = history[-2]
    return prior.release_name, prior.version


def rollback(manager: DeploymentManager, environment: str, timestamp: float) -> DeploymentManager:
    """Re-deploys the release that was active in ``environment`` before the current one.

    Raises:
        DeploymentManagerInputError: If ``environment`` has no active release,
            or has never had a previous one.
    """
    if active_release(manager, environment) is None:
        raise DeploymentManagerInputError(
            f"Environment '{environment}' has no active release to roll back."
        )

    target = previous_release(manager, environment)
    if target is None:
        raise DeploymentManagerInputError(
            f"Environment '{environment}' has no previous release to roll back to."
        )

    name, version = target
    release = get_release(manager, name, version)
    return record_deployment(manager, environment, release, is_rollback=True, timestamp=timestamp)
