"""Stage promotion for registered model versions.

Every move goes through :func:`~alphalab.model_registry.stages.validate_transition`,
so the legal moves are the declared ones and nothing else. Promoting a version
to ``PRODUCTION`` automatically archives whichever version of the same model was
previously in ``PRODUCTION`` -- a model has at most one production version at a
time. Every transition, including that automatic archival, is appended to
``ModelRegistry.promotions``.
"""

from dataclasses import replace

from alphalab.common.append_log import AppendOnlyLog
from alphalab.model_registry.registry import (
    ModelRegistry,
    ModelStage,
    ModelVersion,
    PromotionRecord,
    get_version,
    list_versions,
    replace_version,
)
from alphalab.model_registry.stages import validate_transition

__all__ = ["production_version", "promote", "staging_version", "versions_in_stage"]


def _transition(
    registry: ModelRegistry, version: ModelVersion, to_stage: ModelStage, timestamp: float
) -> ModelRegistry:
    """Apply one already-validated stage move, updating the indexes with it."""

    record = PromotionRecord(
        name=version.name,
        version=version.version,
        from_stage=version.stage,
        to_stage=to_stage,
        timestamp=timestamp,
    )
    moved = replace_version(registry, replace(version, stage=to_stage))

    production = moved.production
    production_line = moved.production_line
    if to_stage is ModelStage.PRODUCTION:
        production = production.set(version.name, version.version)
        line = production_line.get(version.name, AppendOnlyLog[int]())
        production_line = production_line.set(version.name, line.append(version.version))
    elif version.stage is ModelStage.PRODUCTION and version.name in production:
        production = production.delete(version.name)

    return replace(
        moved,
        promotions=moved.promotions.append(record),
        production=production,
        production_line=production_line,
    )


def promote(
    registry: ModelRegistry, name: str, version: int, stage: ModelStage, timestamp: float
) -> ModelRegistry:
    """Moves ``name`` version ``version`` to ``stage``.

    The move must be one :mod:`alphalab.model_registry.stages` declares legal.
    Promoting to ``PRODUCTION`` first archives the current production version of
    the same model, if there is one.

    Raises:
        ModelRegistryInputError: If the version is unknown, or the move is not a
            legal transition -- including a move to ``NONE``, a move to the
            stage the version is already in, and an archived version returning
            to ``PRODUCTION`` when it is not the version to roll back to.
    """
    target = get_version(registry, name, version)
    validate_transition(registry, name, version, target.stage, stage)

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
    :func:`~alphalab.model_registry.registry.get_version`. Read from the
    registry's production index, so this is O(1) and safe to call from a write
    path.
    """
    version = registry.production.get(name)
    if version is None:
        return None
    return get_version(registry, name, version)


def staging_version(registry: ModelRegistry, name: str) -> ModelVersion | None:
    """Returns the most recent ``STAGING`` version of ``name``, or ``None``."""
    versions = registry.versions.get(name)
    if versions is None:
        return None
    staged = [v for v in versions.values() if v.stage is ModelStage.STAGING]
    return staged[-1] if staged else None
