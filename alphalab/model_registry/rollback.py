"""Rolling a model's production pointer back to its previous version.

Rollback is defined purely in terms of the ``ModelRegistry.promotions`` audit
log: the version to roll back to is the one that held ``PRODUCTION`` immediately
before the current production version was promoted into it.
"""

from alphalab.model_registry.exceptions import ModelRegistryInputError
from alphalab.model_registry.promotion import production_version, promote
from alphalab.model_registry.registry import (
    ModelRegistry,
    ModelStage,
    PromotionRecord,
)


def promotion_history(
    registry: ModelRegistry, name: str | None = None
) -> tuple[PromotionRecord, ...]:
    """Returns stage transitions in the order they happened.

    With ``name`` given, only that model's transitions are returned.
    """
    if name is None:
        return registry.promotions
    return tuple(record for record in registry.promotions if record.name == name)


def previous_production_version(registry: ModelRegistry, name: str) -> int | None:
    """Returns the version number that was in ``PRODUCTION`` before the current one.

    ``None`` if ``name`` has no current production version, or has never had a
    different one.
    """
    current = production_version(registry, name)
    if current is None:
        return None

    history = promotion_history(registry, name)
    promoted_current_at: int | None = None
    for index in range(len(history) - 1, -1, -1):
        record = history[index]
        if record.version == current.version and record.to_stage is ModelStage.PRODUCTION:
            promoted_current_at = index
            break
    if promoted_current_at is None:
        return None

    for index in range(promoted_current_at - 1, -1, -1):
        record = history[index]
        if record.to_stage is ModelStage.PRODUCTION and record.version != current.version:
            return record.version
    return None


def rollback(registry: ModelRegistry, name: str, timestamp: float) -> ModelRegistry:
    """Archives ``name``'s current production version and restores the previous one.

    Raises:
        ModelRegistryInputError: If ``name`` has no current production version,
            or has never had a different production version to return to.
    """
    current = production_version(registry, name)
    if current is None:
        raise ModelRegistryInputError(f"Model '{name}' has no production version to roll back.")

    previous = previous_production_version(registry, name)
    if previous is None:
        raise ModelRegistryInputError(
            f"Model '{name}' has no previous production version to roll back to."
        )

    archived = promote(registry, name, current.version, ModelStage.ARCHIVED, timestamp)
    return promote(archived, name, previous, ModelStage.PRODUCTION, timestamp)
