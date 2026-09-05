"""The declared stage transitions of a registered version.

Before v2.4 the legal moves were implicit: any target other than ``NONE``, from
any stage, provided it was not the stage the version was already in. That let a
version move arbitrarily -- including out of ``ARCHIVED`` and back into
``PRODUCTION`` when it had never been in production at all, which is not a
restore but a resurrection, and is exactly the move a rollback must be
distinguishable from.

The table below states every legal move once, and two rules make the remaining
moves explicit rather than merely absent:

``PRODUCTION -> STAGING`` is illegal
    A live version leaves production by being archived, or by being replaced by
    another version's promotion, which archives it. Quietly demoting the live
    version to staging leaves the model with nothing in production and no
    record that anything was taken down.

``ARCHIVED -> PRODUCTION`` is legal only for the previous production version
    That is what :func:`~alphalab.model_registry.rollback.rollback` does: return
    to the version that held production immediately before the current one. Any
    other archived version reaching production would be a version whose
    retirement was undone with no evidence that it was ever fit to be live.

``NONE -> PRODUCTION`` stays legal. The registry is mechanism: it records what
happened and refuses what is incoherent. Requiring evidence, a staging soak or
an approval before something goes live is policy, and policy lives in
:mod:`alphalab.lifecycle`, which gates the promotion before calling this.
"""

from collections.abc import Mapping

from alphalab.model_registry.exceptions import ModelRegistryInputError
from alphalab.model_registry.registry import ModelRegistry, ModelStage

__all__ = [
    "LEGAL_TRANSITIONS",
    "illegal_stage_move",
    "previous_production_version",
    "validate_transition",
]

#: Every legal stage move. A stage absent from a target set cannot be reached
#: from that stage; ``NONE`` is absent from every one, so it is initial-only.
LEGAL_TRANSITIONS: Mapping[ModelStage, frozenset[ModelStage]] = {
    ModelStage.NONE: frozenset({ModelStage.STAGING, ModelStage.PRODUCTION, ModelStage.ARCHIVED}),
    ModelStage.STAGING: frozenset({ModelStage.PRODUCTION, ModelStage.ARCHIVED}),
    ModelStage.PRODUCTION: frozenset({ModelStage.ARCHIVED}),
    ModelStage.ARCHIVED: frozenset({ModelStage.STAGING, ModelStage.PRODUCTION}),
}


def illegal_stage_move(current: ModelStage, target: ModelStage) -> str | None:
    """Returns why moving from ``current`` to ``target`` is illegal, or ``None``.

    The table check alone, with no registry and no subject, so the two things
    that stage versions -- the model registry and
    :mod:`alphalab.lifecycle.promotion` -- share one definition of which moves
    exist instead of writing the table twice. It returns a reason rather than
    raising so each caller raises its own domain exception with its own subject
    in the message.
    """

    if current is target:
        return f"is already in stage {target.name}"

    allowed = LEGAL_TRANSITIONS[current]
    if target not in allowed:
        reachable = ", ".join(sorted(stage.name for stage in allowed))
        return (
            f"cannot move from {current.name} to {target.name}; "
            f"{current.name} can only move to {reachable}"
        )
    return None


def previous_production_version(registry: ModelRegistry, name: str) -> int | None:
    """Returns the version that held ``PRODUCTION`` before the current holder.

    ``None`` if ``name`` has never had two different production versions. Read
    from ``ModelRegistry.production_line``, the ordered record of which versions
    have held production, so this is O(1) rather than a scan of the promotion
    log.
    """
    line = registry.production_line.get(name)
    if line is None or len(line) < 2:
        return None
    return line[-2]


def validate_transition(
    registry: ModelRegistry, name: str, version: int, current: ModelStage, target: ModelStage
) -> None:
    """Raises unless moving ``name`` version ``version`` to ``target`` is legal.

    Raises:
        ModelRegistryInputError: If ``target`` is the version's current stage,
            is unreachable from it, or is ``PRODUCTION`` reached from
            ``ARCHIVED`` by a version that is not the one to roll back to.
    """
    reason = illegal_stage_move(current, target)
    if reason is not None:
        raise ModelRegistryInputError(f"Model '{name}' version {version} {reason}.")

    if current is ModelStage.ARCHIVED and target is ModelStage.PRODUCTION:
        restorable = previous_production_version(registry, name)
        if restorable != version:
            raise ModelRegistryInputError(
                f"Model '{name}' version {version} is ARCHIVED and is not the version "
                f"to roll back to ("
                f"{'none' if restorable is None else restorable}); an archived version "
                "returns to PRODUCTION only as a rollback of the version that replaced it. "
                "Promote it to STAGING first to put it back into circulation."
            )
