"""Gated promotion of a strategy version.

:mod:`alphalab.model_registry` refuses moves that are *incoherent* -- a
demotion out of production, a resurrection of an archived version that was never
live. This module refuses moves that are *unjustified*, which is the part
policy owns:

``NONE -> STAGING`` / ``ARCHIVED -> STAGING``
    Requires evidence that passed a stated policy, and requires the model
    version the strategy runs, if it runs one, to be staged itself. A strategy
    cannot be more validated than the model inside it.
``* -> PRODUCTION``
    Refused here entirely. A strategy version reaches production by being
    deployed, and only :mod:`alphalab.lifecycle.deployment` can do that, so the
    deployment ledger is the only thing that ever puts a version live. Two ways
    to reach production would be two answers to "what is running".
``* -> ARCHIVED``
    Allowed from ``NONE`` and ``STAGING``. Refused from ``PRODUCTION`` while the
    version is still active somewhere: retiring what is live is a rollback or a
    replacement, not a stage edit.
``* -> NONE``
    Refused. ``NONE`` is where a version is registered, and nothing returns to
    it.

Every accepted move appends a :class:`~alphalab.lifecycle.strategy_version.StrategyPromotionRecord`
saying what justified it, so a promoted version records what it passed rather
than only that it passed.
"""

from __future__ import annotations

from dataclasses import replace

from alphalab.lifecycle.evidence import (
    ValidationEvidence,
    ValidationOutcome,
    ValidationPolicy,
    evaluate_policy,
)
from alphalab.lifecycle.exceptions import LifecycleInputError, LifecycleTransitionError
from alphalab.lifecycle.state import LifecycleState
from alphalab.lifecycle.strategy_version import (
    StrategyPromotionRecord,
    StrategyVersion,
    get_strategy_version,
    replace_strategy_version,
)
from alphalab.lifecycle.views import environments_running
from alphalab.model_registry.registry import ModelStage, get_version
from alphalab.model_registry.stages import illegal_stage_move

__all__ = [
    "STAGEABLE_MODEL_STAGES",
    "promote_strategy_version",
    "record_evidence",
    "record_stage_change",
    "retire_strategy_version",
    "validate_strategy_version",
]

#: Model stages a strategy version is allowed to be promoted on top of. A model
#: still at ``NONE`` has been registered and nothing more; an ``ARCHIVED`` one
#: was retired, and shipping it again starts by re-staging the model.
STAGEABLE_MODEL_STAGES = (ModelStage.STAGING, ModelStage.PRODUCTION)


def record_evidence(state: LifecycleState, evidence: ValidationEvidence) -> LifecycleState:
    """Stores a piece of evidence so promotions can refer to it.

    Recording the same evidence twice is a no-op rather than an error: the id is
    a digest of the content, so a second copy is the same measurement and
    storing it again changes nothing.

    Raises:
        LifecycleInputError: If the id is already held by *different* content --
            a hand-built id or a digest collision, either of which would
            silently replace one measurement with another.
    """
    existing = state.evidence.get(evidence.evidence_id)
    if existing is not None:
        if existing != evidence:
            raise LifecycleInputError(
                f"Evidence id '{evidence.evidence_id}' is already held by different "
                "content; ids are digests of the evidence, so this is either a "
                "hand-built id or a collision. Refusing to replace it."
            )
        return state

    return replace(state, evidence=state.evidence.set(evidence.evidence_id, evidence))


def validate_strategy_version(
    state: LifecycleState, name: str, version: int, policy: ValidationPolicy, evidence_id: str
) -> ValidationOutcome:
    """Checks recorded evidence against ``policy`` for one strategy version.

    A pure read: it produces the outcome and changes nothing.
    :func:`promote_strategy_version` runs the same check and refuses to promote
    unless it passes, so a caller can ask "would this promotion be allowed?"
    without attempting it.

    Raises:
        LifecycleInputError: If the version or the evidence is unknown, or the
            evidence is about a different subject -- evidence for one strategy
            version says nothing about another, and letting it through would be
            the whole gate defeated by a copy-pasted id.
    """
    strategy = get_strategy_version(state.strategies, name, version)
    evidence = state.evidence.get(evidence_id)
    if evidence is None:
        raise LifecycleInputError(f"No evidence recorded under id '{evidence_id}'.")

    subject = str(strategy.ref)
    if evidence.subject != subject:
        raise LifecycleInputError(
            f"Evidence '{evidence_id}' is about '{evidence.subject}', not '{subject}'."
        )

    return evaluate_policy(policy, evidence)


def record_stage_change(
    state: LifecycleState,
    strategy: StrategyVersion,
    to_stage: ModelStage,
    reason: str,
    timestamp: float,
    evidence_id: str | None = None,
    policy_id: str | None = None,
) -> LifecycleState:
    """Applies one already-validated stage move and appends its audit record.

    Internal helper shared by this module and
    :mod:`alphalab.lifecycle.deployment`, in the same way
    :func:`~alphalab.model_registry.registry.replace_version` is shared across
    the model registry's modules. It validates nothing: every caller has already
    established that the move is both legal and justified, and doing the check
    twice would put the rules in two places.

    ``evidence_id`` and ``policy_id`` are carried through only when the move
    supplies them; a deployment does not restate the evidence a promotion
    already recorded.
    """

    record = StrategyPromotionRecord(
        name=strategy.name,
        version=strategy.version,
        from_stage=strategy.stage,
        to_stage=to_stage,
        reason=reason,
        timestamp=timestamp,
    )
    updated = replace(
        strategy,
        stage=to_stage,
        evidence_id=strategy.evidence_id if evidence_id is None else evidence_id,
        policy_id=strategy.policy_id if policy_id is None else policy_id,
    )
    moved = replace_strategy_version(state.strategies, updated)
    return replace(state, strategies=replace(moved, promotions=moved.promotions.append(record)))


def _refuse_illegal_move(strategy: StrategyVersion, target: ModelStage) -> None:
    reason = illegal_stage_move(strategy.stage, target)
    if reason is not None:
        raise LifecycleTransitionError(
            f"Strategy '{strategy.name}' version {strategy.version} {reason}."
        )


def promote_strategy_version(
    state: LifecycleState,
    name: str,
    version: int,
    policy: ValidationPolicy,
    evidence_id: str,
    timestamp: float,
) -> LifecycleState:
    """Promotes a strategy version to ``STAGING`` on passing evidence.

    Raises:
        LifecycleInputError: If the version or the evidence is unknown, or the
            evidence is about a different subject.
        LifecycleTransitionError: If the version is already staged or is
            deployed, if its model version is not itself staged, or if the
            evidence does not pass the policy. A refusal names every failed
            check.
    """
    strategy = get_strategy_version(state.strategies, name, version)
    _refuse_illegal_move(strategy, ModelStage.STAGING)

    if strategy.model is not None:
        model = get_version(state.models, strategy.model.name, strategy.model.version)
        if model.stage not in STAGEABLE_MODEL_STAGES:
            raise LifecycleTransitionError(
                f"Strategy '{name}' version {version} runs model '{strategy.model}', "
                f"which is in stage {model.stage.name}; a strategy cannot be staged on "
                f"a model that is not itself "
                f"{' or '.join(stage.name for stage in STAGEABLE_MODEL_STAGES)}."
            )

    outcome = validate_strategy_version(state, name, version, policy, evidence_id)
    if not outcome.passed:
        raise LifecycleTransitionError(
            f"Strategy '{name}' version {version} does not pass policy "
            f"'{policy.policy_id}': " + " ".join(outcome.failures)
        )

    return record_stage_change(
        state,
        strategy,
        ModelStage.STAGING,
        reason=f"evidence '{evidence_id}' passed policy '{policy.policy_id}'",
        timestamp=timestamp,
        evidence_id=evidence_id,
        policy_id=policy.policy_id,
    )


def retire_strategy_version(
    state: LifecycleState, name: str, version: int, timestamp: float, reason: str = "retired"
) -> LifecycleState:
    """Archives a strategy version that is not deployed anywhere.

    Raises:
        LifecycleInputError: If the version is unknown.
        LifecycleTransitionError: If it is already archived, or is still active
            in some environment -- taking down what is live is
            :func:`~alphalab.lifecycle.deployment.rollback_environment` or a
            replacing deployment, not a stage edit.
    """
    strategy = get_strategy_version(state.strategies, name, version)
    _refuse_illegal_move(strategy, ModelStage.ARCHIVED)

    running = environments_running(state, name, version)
    if running:
        raise LifecycleTransitionError(
            f"Strategy '{name}' version {version} is still active in "
            f"{', '.join(running)}; roll back or deploy a replacement first."
        )

    return record_stage_change(state, strategy, ModelStage.ARCHIVED, reason, timestamp)
