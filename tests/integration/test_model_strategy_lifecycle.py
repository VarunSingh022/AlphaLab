"""The lifecycle end to end, over the real engines.

Nothing here is stubbed and nothing is a toy. Evidence comes from a real
backtest -- a dataset driven through market -> strategy -> allocation -> risk ->
OMS -> execution -> portfolio -> analytics -- and from a real research
evaluation. The registries are the real ones. The single test that matters is
:func:`test_the_whole_lifecycle_runs_end_to_end`, which walks

    research candidate -> experiment run -> validation evidence ->
    model version -> strategy version -> promotion -> deployment -> rollback

and asserts what each stage actually produced.

The rest establish the two properties that make the walk worth anything: it is
reproducible under a seed, and each gate refuses when its precondition does not
hold.
"""

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.backtesting import BacktestEngine, BacktestResult
from alphalab.common.ids import id_scope
from alphalab.deployment_manager import active_release, deployment_history, verify_checksum
from alphalab.experiment_tracking import (
    ExperimentTracker,
    complete_run,
    log_metrics,
    start_run,
)
from alphalab.lifecycle import (
    COMPONENT_MODEL,
    COMPONENT_STRATEGY,
    LifecycleState,
    LifecycleTransitionError,
    MetricThreshold,
    ModelRef,
    ValidationMethod,
    ValidationPolicy,
    active_model_version,
    active_strategy_version,
    deploy_strategy_version,
    environments_running,
    evidence_from_backtest,
    evidence_from_research,
    get_strategy_version,
    promote_strategy_version,
    record_evidence,
    register_model_version,
    register_strategy,
    rollback_environment,
    validate_strategy_version,
)
from alphalab.model_registry import ModelStage, deployment_metadata, get_version, promote
from alphalab.research import ResearchEngine, ResearchPayload, TradePayload
from alphalab.research_assistant import (
    generate_candidates,
    run_research_workflow,
    to_strategy_definition,
)
from alphalab.research_assistant.generation import StrategyCandidate
from tests.integration.harness import context_factory, scripted_run

#: The execution path identifies a strategy and an asset by UUID: those are
#: `alphalab.core` identities, and deliberately not the lifecycle's. The
#: lifecycle names a strategy *line* -- "ma-crossover" -- and numbers its
#: versions. Keeping the two apart in this test is the point, not an accident:
#: a deployed strategy version is not the same thing as a running instance's id.
#: Both are fixed rather than generated, so the walk reproduces.
ASSET = "6f1a2c74-0b3f-4a54-9d21-1c1a7f5e4b01"
PIPELINE_STRATEGY_ID = "b2c9e5a7-4d6b-4f18-8a3e-2f7d9c0b1e42"
STRATEGY_LINE = "ma-crossover"
MIDS = [
    Decimal("100.005"),
    Decimal("120.007"),
    Decimal("119.003"),
    Decimal("121.009"),
    Decimal("118.001"),
]
#: Buy at the first quote, sell out at the fourth. Two fills, a realised gain.
PLAN = {2.0: Decimal("100"), 5.0: Decimal("-100")}

POLICY = ValidationPolicy(
    "production-v1",
    (
        MetricThreshold("total_return", minimum=0.0),
        MetricThreshold("max_drawdown", maximum=0.50),
    ),
    required_method=ValidationMethod.BACKTEST,
)


def _backtest(seed: int | None = 20240) -> BacktestResult:
    """A real run through the whole execution path."""

    config, dataset, strategy_state = scripted_run(
        PLAN, MIDS, PIPELINE_STRATEGY_ID, ASSET, seed=seed
    )
    return BacktestEngine.run(config, dataset, strategy_state, context_factory)


def _research_state() -> object:
    returns = (0.01, -0.02, 0.03, 0.01, -0.01, 0.02) * 42
    regimes = ("BULL", "BEAR", "BULL", "BULL", "SIDEWAYS", "BULL") * 42
    trades = tuple(
        TradePayload(f"T{i}", ASSET, 100.0, 105.0, 10.0, 50.0, 86400.0) for i in range(100)
    )
    payload = ResearchPayload(STRATEGY_LINE, returns, trades, {"fast": 5.0}, regimes, 1_000_000.0)
    state = ResearchEngine.initialize("RES-1", STRATEGY_LINE, 1.0)
    return ResearchEngine.run_full_research(state, payload, 2.0)


def _candidate() -> StrategyCandidate:
    """The best point of a real research-assistant grid search."""

    def evaluator(candidate: StrategyCandidate) -> Mapping[str, float]:
        # Deterministic and monotone in `fast`: the search has a real answer.
        return {"sharpe": 1.0 + candidate.parameters["fast"] / 100.0}

    workflow = run_research_workflow(
        template=STRATEGY_LINE,
        space={"fast": (5.0, 8.0, 13.0), "slow": (20.0,)},
        evaluator=evaluator,
        objective="sharpe",
        timestamp=1.0,
        tracker=ExperimentTracker(),
    )
    return workflow.best.candidate


def _lifecycle_through_promotion(
    seed: int | None = 20240,
) -> tuple[LifecycleState, ModelRef, str]:
    """Research -> run -> evidence -> model -> strategy version -> STAGING."""

    state = LifecycleState()

    # 1. A research candidate, lifted into the canonical Studio definition.
    candidate = _candidate()
    definition = to_strategy_definition(
        candidate, "MA crossover", "1", "quant", "Moving-average crossover"
    )

    # 2. The experiment run that produced the model.
    result = _backtest(seed)
    tracker, run_id = start_run(state.experiments, STRATEGY_LINE, dict(candidate.parameters), 1.0)
    tracker = log_metrics(tracker, run_id, {"final_equity": float(result.valuation.equity)})
    tracker = complete_run(tracker, run_id, 2.0)
    state = replace(state, experiments=tracker)

    # 3. The model version, citing that run, and staged.
    state, model = register_model_version(
        state, "momentum", object(), 3.0, run_id=run_id, metrics={"fills": len(result.fills)}
    )
    state = replace(
        state, models=promote(state.models, model.name, model.version, ModelStage.STAGING, 4.0)
    )

    # 4. The strategy version.
    state, strategy = register_strategy(
        state, STRATEGY_LINE, definition, 5.0, model=model, run_id=run_id
    )

    # 5. Evidence from the real run, and the promotion it justifies.
    evidence = evidence_from_backtest(result, str(strategy), "DS", 6.0)
    state = record_evidence(state, evidence)
    state = promote_strategy_version(
        state, strategy.name, strategy.version, POLICY, evidence.evidence_id, 7.0
    )
    return state, model, evidence.evidence_id


# --------------------------------------------------------------------------- #
# The walk
# --------------------------------------------------------------------------- #


def test_the_whole_lifecycle_runs_end_to_end() -> None:
    state, model, evidence_id = _lifecycle_through_promotion()
    strategy = get_strategy_version(state.strategies, STRATEGY_LINE, 1)

    # The candidate's parameters reached the strategy version unchanged.
    assert strategy.definition.parameters == {"fast": 13.0, "slow": 20.0}
    assert strategy.stage is ModelStage.STAGING
    assert strategy.model == model
    assert strategy.evidence_id == evidence_id
    assert strategy.policy_id == "production-v1"

    # The evidence is the real run's analytics, not numbers typed in.
    evidence = state.evidence[evidence_id]
    assert evidence.method is ValidationMethod.BACKTEST
    assert evidence.seed == 20240
    assert evidence.source_id  # the PerformanceReport it came from
    assert evidence.metrics["total_return"] > 0.0

    # Deploy: the ledger, not a flag, says what is live.
    state, deployment = deploy_strategy_version(
        state, STRATEGY_LINE, 1, "paper", 8.0, deployed_by="ci"
    )
    assert deployment.environment == "paper"
    active = active_strategy_version(state, "paper")
    assert active is not None and active.ref == strategy.ref
    assert active_model_version(state, "paper") == model

    release = active_release(state.deployments, "paper")
    assert release is not None
    assert verify_checksum(release)
    assert release.components[COMPONENT_STRATEGY] == "ma-crossover@1"
    assert release.components[COMPONENT_MODEL] == "momentum@1"

    note = deployment_metadata(state.models, model.name, model.version)
    assert note is not None and note.environment == "paper"

    # A second version, promoted on its own evidence, replaces the first.
    result = _backtest()
    state, second = register_strategy(
        state,
        STRATEGY_LINE,
        replace(strategy.definition, version="2", parameters={"fast": 8.0, "slow": 20.0}),
        9.0,
        model=model,
        run_id=strategy.run_id,
    )
    second_evidence = evidence_from_backtest(result, str(second), "DS", 10.0)
    state = record_evidence(state, second_evidence)
    state = promote_strategy_version(
        state, second.name, second.version, POLICY, second_evidence.evidence_id, 11.0
    )
    state, _ = deploy_strategy_version(state, second.name, second.version, "paper", 12.0)

    assert get_strategy_version(state.strategies, STRATEGY_LINE, 1).stage is ModelStage.ARCHIVED
    assert get_strategy_version(state.strategies, STRATEGY_LINE, 2).stage is ModelStage.PRODUCTION

    # Rollback returns the environment to the previously live version.
    state, restored = rollback_environment(state, "paper", 13.0)
    back = active_strategy_version(state, "paper")

    assert restored.environment == "paper"
    assert back is not None and back.version == 1
    assert get_strategy_version(state.strategies, STRATEGY_LINE, 1).stage is ModelStage.PRODUCTION
    assert get_strategy_version(state.strategies, STRATEGY_LINE, 2).stage is ModelStage.ARCHIVED
    assert environments_running(state, STRATEGY_LINE, 1) == ("paper",)
    assert [record.is_rollback for record in deployment_history(state.deployments, "paper")] == [
        False,
        False,
        True,
    ]

    # Every move is on the record, with what justified it.
    assert len(state.strategies.promotions) == 7
    assert all(record.reason for record in state.strategies.promotions)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_the_same_seeded_walk_produces_the_same_lifecycle() -> None:
    from alphalab.persistence.serializer import serialize

    with id_scope(4242):
        first, _, _ = _lifecycle_through_promotion()
        first, _ = deploy_strategy_version(first, STRATEGY_LINE, 1, "paper", 8.0)
    with id_scope(4242):
        second, _, _ = _lifecycle_through_promotion()
        second, _ = deploy_strategy_version(second, STRATEGY_LINE, 1, "paper", 8.0)

    assert serialize(first) == serialize(second)


def test_evidence_from_the_same_run_has_the_same_id() -> None:
    """Evidence identity is a digest of the measurement, so two extractions of
    one run agree without any coordination."""
    result = _backtest()
    first = evidence_from_backtest(result, "ma-crossover@1", "DS", 6.0)
    second = evidence_from_backtest(result, "ma-crossover@1", "DS", 6.0)

    assert first.evidence_id == second.evidence_id


def test_a_different_seed_produces_different_evidence() -> None:
    """The seed is part of what the evidence attests, so it is part of its id."""
    seeded = evidence_from_backtest(_backtest(20240), "ma-crossover@1", "DS", 6.0)
    other = evidence_from_backtest(_backtest(999), "ma-crossover@1", "DS", 6.0)

    assert seeded.evidence_id != other.evidence_id


# --------------------------------------------------------------------------- #
# Evidence from the research engine
# --------------------------------------------------------------------------- #


def test_a_research_evaluation_can_stand_as_evidence() -> None:
    """The other deterministic producer AlphaLab already had."""
    research = _research_state()
    evidence = evidence_from_research(research, "ma-crossover@1", "DS", 6.0)  # type: ignore[arg-type]

    assert evidence.method is ValidationMethod.RESEARCH
    assert evidence.source_id == "RES-1"
    assert set(evidence.metrics) == {
        "bias_score",
        "capacity_score",
        "confidence_score",
        "generalisation_score",
        "overall_score",
        "robustness_score",
        "stability_score",
        "stress_score",
    }


def test_research_evidence_can_gate_a_promotion() -> None:
    state, model, _ = _lifecycle_through_promotion()
    state, third = register_strategy(
        state,
        STRATEGY_LINE,
        get_strategy_version(state.strategies, STRATEGY_LINE, 1).definition,
        9.0,
        model=model,
    )
    evidence = evidence_from_research(_research_state(), str(third), "DS", 10.0)  # type: ignore[arg-type]
    state = record_evidence(state, evidence)
    policy = ValidationPolicy(
        "research-v1",
        (MetricThreshold("overall_score", minimum=0.0),),
        required_method=ValidationMethod.RESEARCH,
    )
    state = promote_strategy_version(
        state, third.name, third.version, policy, evidence.evidence_id, 11.0
    )

    promoted = get_strategy_version(state.strategies, third.name, third.version)
    assert promoted.stage is ModelStage.STAGING
    assert promoted.policy_id == "research-v1"


def test_backtest_evidence_does_not_satisfy_a_research_policy() -> None:
    """`required_method` is what stops a policy being met by the wrong thing."""
    state, _, evidence_id = _lifecycle_through_promotion()
    policy = ValidationPolicy(
        "research-v1",
        (MetricThreshold("overall_score", minimum=0.0),),
        required_method=ValidationMethod.RESEARCH,
    )
    outcome = validate_strategy_version(state, STRATEGY_LINE, 1, policy, evidence_id)

    assert not outcome.passed
    assert "requires RESEARCH evidence" in outcome.failures[0]


# --------------------------------------------------------------------------- #
# The gates, over the real path
# --------------------------------------------------------------------------- #


def test_a_run_that_compiled_no_analytics_cannot_stand_as_evidence() -> None:
    """Evidence without measurements is refused, not recorded as an empty pass."""
    config, dataset, strategy_state = scripted_run(
        PLAN, MIDS, PIPELINE_STRATEGY_ID, ASSET, compile_analytics=False
    )
    result = BacktestEngine.run(config, dataset, strategy_state, context_factory)

    from alphalab.lifecycle import LifecycleInputError

    with pytest.raises(LifecycleInputError, match="compiled no performance report"):
        evidence_from_backtest(result, "ma-crossover@1", "DS", 6.0)


def test_a_losing_run_does_not_pass_a_policy_that_asks_for_a_gain() -> None:
    """The gate is the numbers the engines produced, not an assertion about them."""
    state = LifecycleState()
    definition = to_strategy_definition(
        generate_candidates(STRATEGY_LINE, {"fast": (5.0,), "slow": (20.0,)})[0],
        "MA crossover",
        "1",
        "quant",
        "d",
    )
    state, strategy = register_strategy(state, STRATEGY_LINE, definition, 5.0)

    # Buy the top, sell the bottom.
    losing = {3.0: Decimal("100"), 6.0: Decimal("-100")}
    config, dataset, strategy_state = scripted_run(losing, MIDS, PIPELINE_STRATEGY_ID, ASSET)
    result = BacktestEngine.run(config, dataset, strategy_state, context_factory)
    evidence = evidence_from_backtest(result, str(strategy), "DS", 6.0)
    state = record_evidence(state, evidence)

    assert evidence.metrics["total_return"] < 0.0
    with pytest.raises(LifecycleTransitionError, match="total_return"):
        promote_strategy_version(
            state, strategy.name, strategy.version, POLICY, evidence.evidence_id, 7.0
        )
    assert get_strategy_version(state.strategies, STRATEGY_LINE, 1).stage is ModelStage.NONE


def test_the_model_a_deployed_strategy_runs_is_recoverable_from_the_ledger() -> None:
    state, model, _ = _lifecycle_through_promotion()
    state, _ = deploy_strategy_version(state, STRATEGY_LINE, 1, "live-eu", 8.0)

    assert active_model_version(state, "live-eu") == model
    assert get_version(state.models, model.name, model.version).run_id is not None
