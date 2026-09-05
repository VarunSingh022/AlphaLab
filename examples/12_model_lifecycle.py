"""
AlphaLab Examples
=================

Example 12 : Model and Strategy Lifecycle

Difficulty : Intermediate

Estimated Time : 10 minutes

Topics
------

• Experiment runs and their provenance
• Model versions and artifact references
• Validation evidence and promotion policies
• Strategy versions
• Gated promotion
• Deployment and deterministic rollback

What this shows
---------------

One strategy going from a research candidate to a live deployment, and back:

    research candidate      -> a point in a parameter grid
      -> experiment run     -> parameters and metric history
      -> model version      -> registered, cites the run, staged
      -> strategy version   -> immutable, numbered, references the model
      -> validation evidence-> extracted from a real backtest's analytics
      -> promotion          -> refused without passing evidence
      -> deployment         -> the ledger says what is live
      -> rollback           -> back to the version that ran before

Every stage is one of AlphaLab's existing packages. `alphalab.lifecycle` is
what connects them; it computes no metrics and defines no second model of
anything.

A deployment here is a lifecycle fact, not an operation on a machine: it
records that an environment *should* be running a strategy version. Nothing is
started, and no venue is contacted.

Run

    python examples/12_model_lifecycle.py
"""

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import BacktestConfig, BacktestEngine, MarketDataset
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.experiment_tracking import complete_run, log_metrics, start_run
from alphalab.lifecycle import (
    LifecycleState,
    LifecycleTransitionError,
    MetricThreshold,
    ValidationMethod,
    ValidationPolicy,
    active_strategy_version,
    deploy_strategy_version,
    environments_running,
    evidence_from_backtest,
    get_strategy_version,
    promote_strategy_version,
    record_evidence,
    register_model_version,
    register_strategy,
    rollback_environment,
    validate_strategy_version,
)
from alphalab.market.quote import Quote
from alphalab.model_registry import ArtifactRef, ModelStage, deployment_metadata, promote
from alphalab.portfolio.account import Account
from alphalab.research_assistant import generate_candidates, to_strategy_definition
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime
from alphalab.strategy.runtime import register_strategy as register_instance
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

# The execution path identifies a strategy instance and an asset by UUID. The
# lifecycle names a strategy *line* and numbers its versions. Two different
# identities, deliberately: "ma-crossover@1" is what gets deployed, and the
# UUID is what a running instance is called.
INSTANCE_ID = "b2c9e5a7-4d6b-4f18-8a3e-2f7d9c0b1e42"
ASSET_ID = "6f1a2c74-0b3f-4a54-9d21-1c1a7f5e4b01"
STRATEGY_LINE = "ma-crossover"

START_CASH = Decimal("100000.00")
SEED = 20240905
MIDS = [Decimal("100.005"), Decimal("104.250"), Decimal("109.750"), Decimal("112.500")]

POLICY = ValidationPolicy(
    policy_id="production-v1",
    thresholds=(
        MetricThreshold("total_return", minimum=0.0),
        MetricThreshold("max_drawdown", maximum=0.25),
    ),
    required_method=ValidationMethod.BACKTEST,
)


class BuyAndHold(BaseStrategy):
    """Buys once at the first quote and holds."""

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.timestamp != 1.0:
            return ()
        return (
            Intent(
                strategy_id=INSTANCE_ID,
                instrument=ASSET_ID,
                target=Decimal("100"),
                timestamp=event.quote.timestamp,
            ),
        )


def _context(strategy_id: str) -> StrategyContext:
    class _Clock:
        def now(self) -> float:
            return 0.0

    class _Logger:
        def info(self, msg: str) -> None: ...

        def error(self, msg: str) -> None: ...

    return StrategyContext(
        portfolio=object(),
        market=object(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=object(),
        config={"strategy_id": strategy_id},
        orders=object(),
        history=object(),
        universe=object(),
    )


def _running_instance() -> RuntimeState:
    state = register_instance(create_runtime(), INSTANCE_ID, BuyAndHold())
    instance = state.strategies[INSTANCE_ID]
    configured, _ = RuntimeSupervisor.configure(instance, {}, 0.1)
    initialized, _ = RuntimeSupervisor.initialize(configured, 0.2)
    subscribed, _ = RuntimeSupervisor.subscribe(initialized, frozenset({"quotes"}), 0.3)
    running, _ = RuntimeSupervisor.start(subscribed, 0.4)
    return replace(state, strategies={INSTANCE_ID: running})


def _backtest_config() -> BacktestConfig:
    huge = Decimal("100000000")
    return BacktestConfig(
        pipeline=ExecutionPipelineConfig(
            account=Account("acct-lifecycle", "USD", "Lifecycle Example", 0.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH * Decimal("10"),
                cash_buffer=Decimal("0"),
                strategy_budgets={INSTANCE_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(enforce_integer_quantities=False),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(huge, huge),
                position=PositionLimit(huge, huge),
                exposure=ExposureLimit(huge, huge),
                leverage=LeverageLimit(Decimal("1000")),
                margin=MarginLimit(Decimal("1.00")),
                daily_loss=DailyLossLimit(huge),
                drawdown=DrawdownLimit(Decimal("1.00")),
            ),
            simulator=ExecutionSimulator(),
        ),
        seed=SEED,
        start_timestamp=0.5,
    )


def _dataset(mids: list[Decimal]) -> MarketDataset:
    return MarketDataset.of(
        "DS-LIFECYCLE",
        [
            Quote(
                asset_id=ASSET_ID,
                timestamp=float(index + 1),
                bid=mid,
                ask=mid,
                bid_size=Decimal("1000"),
                ask_size=Decimal("1000"),
                venue="SIM",
                currency="USD",
            )
            for index, mid in enumerate(mids)
        ],
    )


def _run(mids: list[Decimal]) -> Any:
    """A real run through market -> ... -> portfolio -> analytics."""

    return BacktestEngine.run(_backtest_config(), _dataset(mids), _running_instance(), _context)


def main() -> None:
    state = LifecycleState()
    print("=" * 62)
    print("AlphaLab — Model and Strategy Lifecycle")
    print("=" * 62)
    print()

    # ------------------------------------------------------------------
    # Step 1 : A research candidate becomes a recorded experiment run
    # ------------------------------------------------------------------

    candidates = generate_candidates(STRATEGY_LINE, {"fast": (5.0, 8.0), "slow": (20.0,)})
    candidate = candidates[0]
    result = _run(MIDS)

    tracker, run_id = start_run(state.experiments, STRATEGY_LINE, dict(candidate.parameters), 1.0)
    tracker = log_metrics(tracker, run_id, {"final_equity": float(result.valuation.equity)})
    tracker = complete_run(tracker, run_id, 2.0)
    state = replace(state, experiments=tracker)

    print("Step 1 — experiment run")
    print("-" * 62)
    print(f"Candidate        : {candidate.candidate_id} {dict(candidate.parameters)}")
    print(f"Run status       : {state.experiments.runs[run_id].status.name}")
    print(f"Final equity     : {result.valuation.equity}")
    print()

    # ------------------------------------------------------------------
    # Step 2 : A model version that cites that run, and is staged
    # ------------------------------------------------------------------

    state, model = register_model_version(
        state,
        "momentum",
        object(),  # any trained model object; the registry is type-agnostic
        3.0,
        run_id=run_id,
        metrics={"fills": float(len(result.fills))},
        artifact=ArtifactRef("file://models/momentum-1.bin", "application/octet-stream"),
    )
    state = replace(
        state, models=promote(state.models, model.name, model.version, ModelStage.STAGING, 4.0)
    )
    print("Step 2 — model version")
    print("-" * 62)
    print(f"Model            : {model}")
    print(f"Cites run        : {run_id}")
    print(f"Stage            : {ModelStage.STAGING.name}")
    print()

    # ------------------------------------------------------------------
    # Step 3 : A strategy version, from the canonical Studio definition
    # ------------------------------------------------------------------

    definition = to_strategy_definition(
        candidate, "MA crossover", "1", "quant", "Moving-average crossover"
    )
    state, strategy = register_strategy(
        state, STRATEGY_LINE, definition, 5.0, model=model, run_id=run_id
    )
    print("Step 3 — strategy version")
    print("-" * 62)
    print(f"Strategy version : {strategy}")
    print(f"Runs model       : {model}")
    print(
        f"Stage            : {get_strategy_version(state.strategies, *_ref(strategy)).stage.name}"
    )
    print()

    # ------------------------------------------------------------------
    # Step 4 : Evidence from the real run, judged against a stated policy
    # ------------------------------------------------------------------

    evidence = evidence_from_backtest(result, str(strategy), "DS-LIFECYCLE", 6.0)
    state = record_evidence(state, evidence)
    outcome = validate_strategy_version(state, *_ref(strategy), POLICY, evidence.evidence_id)

    print("Step 4 — validation evidence")
    print("-" * 62)
    print(f"Evidence id      : {evidence.evidence_id[:16]}…  (a digest of its own content)")
    print(f"From             : {evidence.method.name}, seed {evidence.seed}")
    print(f"total_return     : {evidence.metrics['total_return']:.6f}")
    print(f"max_drawdown     : {evidence.metrics['max_drawdown']:.6f}")
    print(f"Policy           : {POLICY.policy_id} -> passed={outcome.passed}")
    print()

    # ------------------------------------------------------------------
    # Step 5 : Deploying before promotion is refused
    # ------------------------------------------------------------------

    print("Step 5 — the gate")
    print("-" * 62)
    try:
        deploy_strategy_version(state, *_ref(strategy), "paper", 7.0)
    except LifecycleTransitionError as error:
        print(f"Refused          : {error}")

    state = promote_strategy_version(state, *_ref(strategy), POLICY, evidence.evidence_id, 7.0)
    print(
        f"After promotion  : {get_strategy_version(state.strategies, *_ref(strategy)).stage.name}"
    )
    print()

    # ------------------------------------------------------------------
    # Step 6 : Deployment — the ledger is what says something is live
    # ------------------------------------------------------------------

    state, deployment = deploy_strategy_version(
        state, *_ref(strategy), "paper", 8.0, deployed_by="example"
    )
    live = active_strategy_version(state, "paper")
    assert live is not None
    note = deployment_metadata(state.models, model.name, model.version)

    print("Step 6 — deployment")
    print("-" * 62)
    print(f"Deployment       : {deployment}")
    print(f"Active in paper  : {live.ref} (stage {live.stage.name})")
    where = f"{note.environment} @ {note.deployed_at}" if note else "none"
    print(f"Model note       : {where}")
    print()

    # ------------------------------------------------------------------
    # Step 7 : A second version replaces the first
    # ------------------------------------------------------------------

    second_definition = to_strategy_definition(
        candidates[1], "MA crossover", "2", "quant", "Moving-average crossover"
    )
    state, second = register_strategy(
        state, STRATEGY_LINE, second_definition, 9.0, model=model, run_id=run_id
    )
    second_evidence = evidence_from_backtest(_run(MIDS), str(second), "DS-LIFECYCLE", 10.0)
    state = record_evidence(state, second_evidence)
    state = promote_strategy_version(
        state, *_ref(second), POLICY, second_evidence.evidence_id, 11.0
    )
    state, _ = deploy_strategy_version(state, *_ref(second), "paper", 12.0)

    print("Step 7 — a replacement")
    print("-" * 62)
    print(
        f"v1 stage         : {get_strategy_version(state.strategies, *_ref(strategy)).stage.name}"
    )
    print(f"v2 stage         : {get_strategy_version(state.strategies, *_ref(second)).stage.name}")
    print()

    # ------------------------------------------------------------------
    # Step 8 : Rollback
    # ------------------------------------------------------------------

    state, restored = rollback_environment(state, "paper", 13.0, deployed_by="oncall")
    back = active_strategy_version(state, "paper")
    assert back is not None

    print("Step 8 — rollback")
    print("-" * 62)
    print(f"Restored         : {restored}")
    print(f"Active in paper  : {back.ref}")
    print(
        f"v1 stage         : {get_strategy_version(state.strategies, *_ref(strategy)).stage.name}"
    )
    print(f"v2 stage         : {get_strategy_version(state.strategies, *_ref(second)).stage.name}")
    print(f"v1 running in    : {environments_running(state, *_ref(strategy))}")
    print()

    print("The audit trail")
    print("-" * 62)
    for record in state.strategies.promotions:
        print(
            f"  v{record.version} {record.from_stage.name:>10} -> "
            f"{record.to_stage.name:<10} {record.reason}"
        )


def _ref(reference: Any) -> tuple[str, int]:
    """A StrategyVersionRef as the (name, version) pair the API takes."""

    return reference.name, reference.version


if __name__ == "__main__":
    main()
