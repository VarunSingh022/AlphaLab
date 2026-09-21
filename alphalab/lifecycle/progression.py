"""The strategy's progression from an idea to something that trades, and back.

AlphaLab already had two state machines with "lifecycle" in their name, and
neither answers this question:

==================================================== =========================================
:class:`~alphalab.model_registry.registry.ModelStage` Is this *registered artifact* promotable?
                                                     ``NONE / STAGING / PRODUCTION / ARCHIVED``,
                                                     shared by model versions and strategy
                                                     versions because it is the same question
                                                     about a different artifact.
:class:`alphalab.strategy.state.LifecycleState`      Is this *instance inside a session*
                                                     running? ``CREATED`` through ``DISPOSED``.
                                                     A deployed version starts and stops many
                                                     times without anything else changing.
==================================================== =========================================

This module adds the third axis, and it is genuinely a different one: **how far
along is this strategy on the way from research to live money?** ``ModelStage``
cannot express it, and stretching it would have been worse than a second
vocabulary rather than better. Research, backtest and validation are all
``NONE`` -- a version that has never been measured and one that has passed a
walk-forward are the same stage. Paper and live are both ``PRODUCTION``,
separated only by the free-form environment string a deployment named. And
``PAUSED`` has no representation at all, because a registry entry does not
pause.

So the three coexist and are kept apart on purpose:

.. code-block:: text

    ModelStage             promotability of the artifact     (unchanged)
    StrategyLifecycleStage maturity of the strategy          (this module)
    LifecycleState         the instance in one session       (unchanged)

One stage per strategy version, not per environment
----------------------------------------------------

``ROADMAP.md`` states a deliberate boundary: "a strategy version has one stage
across all environments... a policy that differs between ``paper`` and
``live-eu`` is not expressible, and making it so would put a second source of
truth beside the deployment ledger." That boundary is **unchanged here**. A
:class:`StrategyProgression` is keyed by
:class:`~alphalab.lifecycle.identity.StrategyVersionRef` and names no
environment; ``PAPER`` and ``LIVE`` are maturity, not addresses. What is live
*where* remains exactly one thing -- the deployment ledger, read through
:func:`~alphalab.lifecycle.views.active_strategy_version`.

Which is why this is a declaration, not a fact about a venue
-------------------------------------------------------------

Recording ``LIVE`` asserts that a strategy has reached the live stage of its
progression. It does not put anything live, start a process or reach a venue,
and a progression that says ``LIVE`` while the ledger names nobody is a real
disagreement that :func:`progression_conflicts` reports rather than resolves --
the same position :mod:`alphalab.lifecycle.execution` takes when a version's
stage and the ledger disagree.

Where it lives
--------------

Nowhere in :class:`~alphalab.lifecycle.state.LifecycleState`. That state has one
snapshot owner and one schema literal, and AlphaLab has no migration framework,
so a new field there would make every payload written before v3.5 unreadable to
serve a value the caller can simply hold. It is the same decision ADR-0030
decision 2 records for ``RunState``, and the same shape
:class:`~alphalab.lifecycle.execution.RunAuthorization` already has: a value
produced by a function, recorded by whoever asked for it.

Pausing, and resuming to where you were
----------------------------------------

Only a *running* stage can be paused -- see :data:`PAUSABLE_STAGES`. Pausing
research means nothing, and allowing it would make ``PAUSED`` a second name for
"not being worked on".

Resumption returns to the stage the pause interrupted, and that stage is
**derived from the history** rather than stored beside it: the history is
append-only, so the last transition into ``PAUSED`` is an unambiguous record of
where it came from, and a stored copy would be a second home for one fact. The
consequence is the property that matters: a paper strategy that pauses cannot
resume into ``LIVE``. Promotion through a pause is the one way this vocabulary
could have been used to skip a stage, and :func:`advance_progression` refuses
it by name.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Final

from alphalab.common.append_log import AppendOnlyLog
from alphalab.lifecycle.exceptions import LifecycleInputError, LifecycleTransitionError
from alphalab.lifecycle.identity import StrategyVersionRef
from alphalab.lifecycle.strategy_version import StrategyVersion
from alphalab.model_registry.registry import ModelStage

__all__ = [
    "INITIAL_STAGE",
    "LEGAL_PROGRESSION_TRANSITIONS",
    "PAUSABLE_STAGES",
    "PROGRESSION_MODEL_STAGES",
    "StageTransition",
    "StrategyLifecycleStage",
    "StrategyProgression",
    "advance_progression",
    "archive_progression",
    "begin_progression",
    "illegal_progression_move",
    "pause_progression",
    "progression_conflicts",
    "resume_progression",
    "resume_target",
]


class StrategyLifecycleStage(Enum):
    """How far a strategy version has travelled towards trading live money.

    The eight stages the roadmap names, in the order it names them. Each is a
    claim somebody has to be willing to make about the strategy, which is why
    none of them is reached by an internal side effect.
    """

    #: An idea being worked on. Nothing has been measured yet.
    RESEARCH = auto()

    #: It has been run through the execution path over historical data.
    BACKTEST = auto()

    #: The backtest has been challenged -- walk-forward, cross-validation,
    #: robustness, overfitting diagnostics. :mod:`alphalab.research` is where
    #: those measurements come from and :mod:`alphalab.lifecycle.evidence` is
    #: how they are recorded.
    VALIDATION = auto()

    #: Running against live data without live money.
    PAPER = auto()

    #: Cleared to go live, and not live. The stage that exists because "we would
    #: run this tomorrow" and "this is running" are different states and were
    #: previously the same one.
    PRODUCTION_CANDIDATE = auto()

    #: Trading live money.
    LIVE = auto()

    #: Held. Reached only from a running stage, and returns to the stage it
    #: interrupted. See :func:`resume_progression`.
    PAUSED = auto()

    #: Retired. Terminal: nothing leaves it. A retired strategy that comes back
    #: is a new version of the line, which is what
    #: :func:`~alphalab.lifecycle.strategy_version.register_strategy_version`
    #: produces, and it starts again at ``RESEARCH``.
    ARCHIVED = auto()


#: The stage every progression starts in. Registering a strategy is not a claim
#: that anything about it has been measured.
INITIAL_STAGE: Final = StrategyLifecycleStage.RESEARCH

#: Stages a strategy can be paused *from*. A pause holds something that is
#: running; there is nothing to hold about an idea being researched.
PAUSABLE_STAGES: Final = (
    StrategyLifecycleStage.PAPER,
    StrategyLifecycleStage.PRODUCTION_CANDIDATE,
    StrategyLifecycleStage.LIVE,
)

#: Every legal move, stated once. A stage absent from a target set cannot be
#: reached from that stage.
#:
#: Backward moves are legal and deliberate: a paper run that behaves unlike its
#: backtest sends the strategy back to ``VALIDATION``, and refusing that would
#: mean the only way to record a step backwards is to archive the version and
#: register another one -- which loses the line between what was learned and
#: what replaced it. What is *not* legal is skipping forward: every stage is
#: reached from the one before it, and ``PAUSED`` is not a route around that
#: (:func:`advance_progression` narrows the moves out of ``PAUSED`` to the one
#: stage the pause interrupted).
LEGAL_PROGRESSION_TRANSITIONS: Final[
    dict[StrategyLifecycleStage, frozenset[StrategyLifecycleStage]]
] = {
    StrategyLifecycleStage.RESEARCH: frozenset(
        {StrategyLifecycleStage.BACKTEST, StrategyLifecycleStage.ARCHIVED}
    ),
    StrategyLifecycleStage.BACKTEST: frozenset(
        {
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.RESEARCH,
            StrategyLifecycleStage.ARCHIVED,
        }
    ),
    StrategyLifecycleStage.VALIDATION: frozenset(
        {
            StrategyLifecycleStage.PAPER,
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.ARCHIVED,
        }
    ),
    StrategyLifecycleStage.PAPER: frozenset(
        {
            StrategyLifecycleStage.PRODUCTION_CANDIDATE,
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.PAUSED,
            StrategyLifecycleStage.ARCHIVED,
        }
    ),
    StrategyLifecycleStage.PRODUCTION_CANDIDATE: frozenset(
        {
            StrategyLifecycleStage.LIVE,
            StrategyLifecycleStage.PAPER,
            StrategyLifecycleStage.PAUSED,
            StrategyLifecycleStage.ARCHIVED,
        }
    ),
    StrategyLifecycleStage.LIVE: frozenset(
        {
            StrategyLifecycleStage.PAUSED,
            StrategyLifecycleStage.PRODUCTION_CANDIDATE,
            StrategyLifecycleStage.ARCHIVED,
        }
    ),
    StrategyLifecycleStage.PAUSED: frozenset({*PAUSABLE_STAGES, StrategyLifecycleStage.ARCHIVED}),
    StrategyLifecycleStage.ARCHIVED: frozenset(),
}

#: The :class:`~alphalab.model_registry.registry.ModelStage` values each
#: progression stage is consistent with, so the two axes can be checked against
#: one another instead of merely coexisting.
#:
#: This is a *relation*, not a conversion, and it is deliberately many-to-many.
#: Three progression stages map to ``NONE`` because the registry cannot tell
#: them apart, and ``PAUSED`` maps to both ``STAGING`` and ``PRODUCTION``
#: because pausing a live strategy does not undeploy it --
#: :func:`~alphalab.lifecycle.deployment.rollback_environment` does that, and it
#: is a separate act with a separate record.
PROGRESSION_MODEL_STAGES: Final[dict[StrategyLifecycleStage, frozenset[ModelStage]]] = {
    StrategyLifecycleStage.RESEARCH: frozenset({ModelStage.NONE}),
    StrategyLifecycleStage.BACKTEST: frozenset({ModelStage.NONE}),
    StrategyLifecycleStage.VALIDATION: frozenset({ModelStage.NONE}),
    StrategyLifecycleStage.PAPER: frozenset({ModelStage.STAGING, ModelStage.PRODUCTION}),
    StrategyLifecycleStage.PRODUCTION_CANDIDATE: frozenset(
        {ModelStage.STAGING, ModelStage.PRODUCTION}
    ),
    StrategyLifecycleStage.LIVE: frozenset({ModelStage.PRODUCTION}),
    StrategyLifecycleStage.PAUSED: frozenset({ModelStage.STAGING, ModelStage.PRODUCTION}),
    StrategyLifecycleStage.ARCHIVED: frozenset({ModelStage.ARCHIVED}),
}


@dataclass(frozen=True, slots=True)
class StageTransition:
    """One recorded move between two progression stages.

    Attributes:
        from_stage: Where it was.
        to_stage: Where it went.
        reason: Why. Free text, and required: a transition nobody explained is
            the audit record this type exists to stop being.
        timestamp: Unix timestamp the move was made. Supplied by the caller;
            nothing here reads a clock.
        actor_id: The ``enterprise.Principal.principal_id`` that made the move,
            or ``""`` for one no principal requested. Spelled exactly as
            :class:`~alphalab.lifecycle.strategy_version.StrategyPromotionRecord`
            spells it, for the reason ADR-0018 gives: the field is a reference,
            and an honest empty value beats a fabricated attribution.

    Raises:
        LifecycleInputError: If ``reason`` is blank.
    """

    from_stage: StrategyLifecycleStage
    to_stage: StrategyLifecycleStage
    reason: str
    timestamp: float
    actor_id: str = ""

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise LifecycleInputError(
                f"A move from {self.from_stage.name} to {self.to_stage.name} states no "
                "reason. A transition record with no reason is a log line, not an audit "
                "trail."
            )


@dataclass(frozen=True, slots=True)
class StrategyProgression:
    """One strategy version's position on the research-to-live axis, with its history.

    Immutable, like every other state in AlphaLab: every move returns a new
    progression and the old one stays readable.

    Attributes:
        reference: The strategy version this describes. A *version*, not a line:
            two versions of one strategy progress independently, which is the
            whole reason :class:`~alphalab.lifecycle.identity.StrategyVersionRef`
            exists.
        stage: Where it is now.
        history: Every move ever applied, in the order it happened. Append-only,
            so :func:`resume_target` can read where a pause came from without a
            second field to keep true.
    """

    reference: StrategyVersionRef
    stage: StrategyLifecycleStage
    history: AppendOnlyLog[StageTransition]

    @property
    def is_terminal(self) -> bool:
        """Whether no further move is possible. True only for ``ARCHIVED``."""

        return not LEGAL_PROGRESSION_TRANSITIONS[self.stage]

    @property
    def is_paused(self) -> bool:
        """Whether this progression is currently held."""

        return self.stage is StrategyLifecycleStage.PAUSED

    @property
    def transitions(self) -> tuple[StageTransition, ...]:
        """The history as a tuple, in the order the moves happened."""

        return self.history.to_tuple()


def begin_progression(reference: StrategyVersionRef) -> StrategyProgression:
    """Start ``reference`` at :data:`INITIAL_STAGE` with an empty history.

    Takes no timestamp, deliberately. Beginning is not a transition -- there is
    no stage it moved from -- and minting a self-referential ``RESEARCH ->
    RESEARCH`` record to have something in the log would put a move in the
    history that nobody made.
    """

    return StrategyProgression(reference=reference, stage=INITIAL_STAGE, history=AppendOnlyLog())


def resume_target(progression: StrategyProgression) -> StrategyLifecycleStage | None:
    """The stage a paused progression would resume into, or ``None``.

    ``None`` when it is not paused, and -- deliberately -- also when it is
    paused with no recorded transition into ``PAUSED``, which is what a
    hand-built progression produces. Guessing a stage for one of those would
    invent the fact the whole resume rule rests on.

    Walks the history backwards **by index** rather than through
    :attr:`StrategyProgression.transitions`, which materializes the whole log.
    A pause is the newest transition in every case that matters -- a pause is
    resumed from, not stepped over -- so this is one lookup there, and the
    materializing version made a strategy that pauses daily cost time quadratic
    in its own history. See ``benchmarks/benchmark_strategy_execution.py``.
    """

    if not progression.is_paused:
        return None
    history = progression.history
    for index in range(len(history) - 1, -1, -1):
        transition = history[index]
        if transition.to_stage is StrategyLifecycleStage.PAUSED:
            return transition.from_stage
    return None


def illegal_progression_move(
    progression: StrategyProgression, target: StrategyLifecycleStage
) -> str | None:
    """Why moving ``progression`` to ``target`` is illegal, or ``None``.

    The rules alone, returning a reason rather than raising, in the same shape
    and for the same purpose as
    :func:`~alphalab.model_registry.stages.illegal_stage_move`: a caller can ask
    "would this be allowed?" without attempting it, and the one site that
    decides is shared with the one that acts.
    """

    if progression.stage is target:
        return f"is already in stage {target.name}"

    allowed = LEGAL_PROGRESSION_TRANSITIONS[progression.stage]
    if not allowed:
        return f"is {progression.stage.name}, which is terminal; nothing moves out of it"
    if target not in allowed:
        reachable = ", ".join(sorted(stage.name for stage in allowed))
        return (
            f"cannot move from {progression.stage.name} to {target.name}; "
            f"{progression.stage.name} can only move to {reachable}"
        )

    if progression.is_paused and target is not StrategyLifecycleStage.ARCHIVED:
        resumes_to = resume_target(progression)
        if resumes_to is None:
            return (
                "is PAUSED with no recorded transition into PAUSED, so the stage it "
                "would resume into is unknown; it can only be archived"
            )
        if target is not resumes_to:
            return (
                f"is PAUSED from {resumes_to.name} and would resume into {target.name}; "
                "a pause returns to the stage it interrupted, and resuming into another "
                "one would be a promotion recorded as a resumption"
            )
    return None


def advance_progression(
    progression: StrategyProgression,
    target: StrategyLifecycleStage,
    reason: str,
    timestamp: float,
    actor_id: str = "",
) -> StrategyProgression:
    """Move ``progression`` to ``target``, recording why and when.

    The one function that changes a stage. :func:`pause_progression`,
    :func:`resume_progression` and :func:`archive_progression` are named
    spellings of a call to this, so there is one place the rules are applied.

    Raises:
        LifecycleTransitionError: If the move is not one
            :func:`illegal_progression_move` permits. Nothing is written.
        LifecycleInputError: If ``reason`` is blank.
    """

    refusal = illegal_progression_move(progression, target)
    if refusal is not None:
        raise LifecycleTransitionError(f"Strategy {progression.reference} {refusal}.")

    transition = StageTransition(
        from_stage=progression.stage,
        to_stage=target,
        reason=reason,
        timestamp=timestamp,
        actor_id=actor_id,
    )
    return replace(progression, stage=target, history=progression.history.append(transition))


def pause_progression(
    progression: StrategyProgression, reason: str, timestamp: float, actor_id: str = ""
) -> StrategyProgression:
    """Hold a running strategy where it is.

    Raises:
        LifecycleTransitionError: If it is not in a :data:`PAUSABLE_STAGES`
            stage. The message says which stages can be paused, because
            "cannot pause" is not actionable on its own.
    """

    if progression.stage not in PAUSABLE_STAGES:
        raise LifecycleTransitionError(
            f"Strategy {progression.reference} is in stage {progression.stage.name}, "
            f"which is not running; only "
            f"{', '.join(stage.name for stage in PAUSABLE_STAGES)} can be paused."
        )
    return advance_progression(
        progression, StrategyLifecycleStage.PAUSED, reason, timestamp, actor_id
    )


def resume_progression(
    progression: StrategyProgression, reason: str, timestamp: float, actor_id: str = ""
) -> StrategyProgression:
    """Return a paused strategy to the stage the pause interrupted.

    The target is derived, never supplied: a caller who could name it could name
    a different one, and a resume that lands somewhere new is a promotion with
    the wrong word on it.

    Raises:
        LifecycleTransitionError: If it is not paused, or is paused with no
            recorded transition into ``PAUSED`` to read a target from.
    """

    if not progression.is_paused:
        raise LifecycleTransitionError(
            f"Strategy {progression.reference} is in stage {progression.stage.name} and "
            "is not paused, so there is nothing to resume."
        )
    target = resume_target(progression)
    if target is None:
        raise LifecycleTransitionError(
            f"Strategy {progression.reference} is PAUSED with no recorded transition "
            "into PAUSED, so the stage it would resume into is unknown. It can be "
            "archived; it cannot be resumed into a stage nobody recorded."
        )
    return advance_progression(progression, target, reason, timestamp, actor_id)


def archive_progression(
    progression: StrategyProgression, reason: str, timestamp: float, actor_id: str = ""
) -> StrategyProgression:
    """Retire a strategy version. Terminal: nothing moves out of ``ARCHIVED``."""

    return advance_progression(
        progression, StrategyLifecycleStage.ARCHIVED, reason, timestamp, actor_id
    )


def progression_conflicts(
    progression: StrategyProgression, version: StrategyVersion
) -> tuple[str, ...]:
    """Everything the progression and the registered version disagree about.

    Empty means they are consistent. Two axes describing one strategy can drift
    -- a version archived in the registry while its progression still says
    ``LIVE`` -- and this **reports** the drift rather than resolving it, because
    which side is right is a question about what actually happened and not one
    a mapping table can answer. That is the same position
    :func:`~alphalab.lifecycle.execution.run_plan` takes when a version's stage
    and the deployment ledger disagree.

    Checks two things: that they describe the same strategy version at all, and
    that the progression's stage is one :data:`PROGRESSION_MODEL_STAGES` says is
    consistent with the version's ``ModelStage``.
    """

    conflicts: list[str] = []
    if progression.reference != version.ref:
        conflicts.append(
            f"the progression is for {progression.reference} and the version is "
            f"{version.ref}; they describe different strategy versions."
        )
        return tuple(conflicts)

    permitted = PROGRESSION_MODEL_STAGES[progression.stage]
    if version.stage not in permitted:
        conflicts.append(
            f"{progression.reference} is at progression stage {progression.stage.name}, "
            f"which is consistent with ModelStage "
            f"{' or '.join(sorted(stage.name for stage in permitted))}, but the "
            f"registered version is {version.stage.name}."
        )
    return tuple(conflicts)
