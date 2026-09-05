"""Rolling a model's production pointer back to its previous version.

Rollback returns a model to the version that held ``PRODUCTION`` immediately
before the current one, and archives the version being replaced. Which version
that is comes from ``ModelRegistry.production_line`` -- the ordered record of
which versions have held production -- so the answer is O(1) and does not depend
on rescanning the whole promotion log.

The restored version is ``ARCHIVED`` at this point, and
:mod:`alphalab.model_registry.stages` allows ``ARCHIVED -> PRODUCTION`` only for
exactly this version. A rollback is therefore the *only* way a retired version
becomes live again, which is what makes "roll back" a different operation from
"promote something old".
"""

from alphalab.model_registry.exceptions import ModelRegistryInputError
from alphalab.model_registry.promotion import production_version, promote
from alphalab.model_registry.registry import ModelRegistry, ModelStage, PromotionRecord
from alphalab.model_registry.stages import previous_production_version

__all__ = ["previous_production_version", "promotion_history", "rollback"]


def promotion_history(
    registry: ModelRegistry, name: str | None = None
) -> tuple[PromotionRecord, ...]:
    """Returns stage transitions in the order they happened.

    With ``name`` given, only that model's transitions are returned.
    """
    if name is None:
        return registry.promotions.to_tuple()
    return tuple(record for record in registry.promotions if record.name == name)


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
