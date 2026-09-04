"""Stage promotion for registered model versions.

Promoting a version to ``PRODUCTION`` automatically archives whichever version
of the same model was previously in ``PRODUCTION`` -- a model has at most one
production version at a time. Every transition, including that automatic
archival, is appended to ``ModelRegistry.promotions``.
"""

from dataclasses import replace

from alphalab.model_registry.exceptions import ModelRegistryInputError
from alphalab.model_registry.registry import (
    ModelRegistry,
    ModelStage,
    ModelVersion,
    PromotionRecord,
    get_version,
    list_versions,
    replace_version,
)

_PROMOTABLE_STAGES = (ModelStage.STAGING, ModelStage.PRODUCTION, ModelStage.ARCHIVED)


def _transition(
    registry: ModelRegistry, version: ModelVersion, to_stage: ModelStage, timestamp: float
) -> ModelRegistry:
    record = PromotionRecord(
        name=version.name,
        version=version.version,
        from_stage=version.stage,
        to_stage=to_stage,
        timestamp=timestamp,
    )
    moved = replace_version(registry, replace(version, stage=to_stage))
    return replace(moved, promotions=(*moved.promotions, record))


def promote(
    registry: ModelRegistry, name: str, version: int, stage: ModelStage, timestamp: float
) -> ModelRegistry:
    """Moves ``name`` version ``version`` to ``stage``.

    ``stage`` must be ``STAGING``, ``PRODUCTION``, or ``ARCHIVED`` -- a version
    cannot be moved back to ``NONE``. Promoting to ``PRODUCTION`` first archives
    the current production version of the same model, if there is one.

    Raises:
        ModelRegistryInputError: If the version is unknown, ``stage`` is
            ``NONE``, or the version is already in ``stage``.
    """
    if stage not in _PROMOTABLE_STAGES:
        raise ModelRegistryInputError(
            f"Cannot promote to {stage.name}; valid targets are "
            f"{', '.join(s.name for s in _PROMOTABLE_STAGES)}."
        )

    target = get_version(registry, name, version)
    if target.stage is stage:
        raise ModelRegistryInputError(
            f"Model '{name}' version {version} is already in stage {stage.name}."
        )

    updated = registry
    if stage is ModelStage.PRODUCTION:
        incumbent = production_version(registry, name)
        if incumbent is not None and incumbent.version != version:
            updated = _transition(updated, incumbent, ModelStage.ARCHIVED, timestamp)

    return _transition(updated, get_version(updated, name, version), stage, timestamp)


def versions_in_stage(
    registry: ModelRegistry, name: str, stage: ModelStage
) -> tuple[ModelVersion, ...]:
    """Returns every version of ``name`` currently in ``stage``, ascending.

    Raises:
        ModelRegistryInputError: If ``name`` has no registered versions.
    """
    return tuple(v for v in list_versions(registry, name) if v.stage is stage)


def production_version(registry: ModelRegistry, name: str) -> ModelVersion | None:
    """Returns the single ``PRODUCTION`` version of ``name``, or ``None``.

    Returns ``None`` both when ``name`` is unknown and when it has no production
    version -- callers that need the stricter distinction use
    :func:`get_version`.
    """
    versions = registry.versions.get(name, ())
    for version in versions:
        if version.stage is ModelStage.PRODUCTION:
            return version
    return None


def staging_version(registry: ModelRegistry, name: str) -> ModelVersion | None:
    """Returns the most recent ``STAGING`` version of ``name``, or ``None``."""
    versions = registry.versions.get(name, ())
    staged = [v for v in versions if v.stage is ModelStage.STAGING]
    return staged[-1] if staged else None
