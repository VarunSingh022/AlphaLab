"""Attaching deployment metadata to staged or production model versions."""

from dataclasses import replace

from alphalab.model_registry.exceptions import ModelRegistryInputError
from alphalab.model_registry.registry import (
    DeploymentMetadata,
    ModelRegistry,
    ModelStage,
    ModelVersion,
    get_version,
    list_versions,
    replace_version,
)

_DEPLOYABLE_STAGES = (ModelStage.STAGING, ModelStage.PRODUCTION)


def set_deployment_metadata(
    registry: ModelRegistry, name: str, version: int, metadata: DeploymentMetadata
) -> ModelRegistry:
    """Records where ``name`` version ``version`` is deployed.

    Replaces any previously recorded metadata for that version. Only ``STAGING``
    and ``PRODUCTION`` versions can carry deployment metadata -- a ``NONE`` or
    ``ARCHIVED`` version is not deployed anywhere.

    Raises:
        ModelRegistryInputError: If the version is unknown, or is not in
            ``STAGING`` or ``PRODUCTION``.
    """
    target = get_version(registry, name, version)
    if target.stage not in _DEPLOYABLE_STAGES:
        raise ModelRegistryInputError(
            f"Model '{name}' version {version} is in stage {target.stage.name}; "
            "only STAGING or PRODUCTION versions can carry deployment metadata."
        )
    return replace_version(registry, replace(target, deployment=metadata))


def deployment_metadata(
    registry: ModelRegistry, name: str, version: int
) -> DeploymentMetadata | None:
    """Returns the deployment metadata for a version, or ``None`` if unset.

    Raises:
        ModelRegistryInputError: If the version is unknown.
    """
    return get_version(registry, name, version).deployment


def deployed_versions(registry: ModelRegistry, name: str) -> tuple[ModelVersion, ...]:
    """Returns every version of ``name`` that has deployment metadata, ascending.

    Raises:
        ModelRegistryInputError: If ``name`` has no registered versions.
    """
    return tuple(v for v in list_versions(registry, name) if v.deployment is not None)
