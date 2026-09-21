"""High-performance benchmark suite for the v3.5 strategy-execution paths.

Five computational paths, each measured on a workload a real deployment
produces rather than on a toy:

* **Lifecycle transition validation** -- a strategy line that is paused and
  resumed for a year, so the transition history is long and the resume target
  has to be resolved against it.
* **Deployment specification validation and identity** -- a book of strategies
  re-specified on every parameter change, where the digest is computed once per
  specification and the coherence check runs over the whole thing.
* **Health evaluation** -- one observation per market event over a session's
  worth of orders and instruments.
* **Comparison** -- a full trading day's fills, expected against paper, against
  live, and all three at once.
* **Reconciliation** -- a book and a venue that mostly agree, which is the
  expensive case: every record is compared rather than short-circuited by a
  missing counterpart.

Each path is measured at two sizes, so the printed ops/sec is readable *and*
the scaling is visible. ``tests/regression/test_v35_complexity.py`` asserts the
growth ratios; this prints the absolute numbers that ratio is made of.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import AssetType, OrderStatus, OrderType, Side, TimeInForce
from alphalab.lifecycle import (
    BrokerRequirements,
    CapitalPolicy,
    ComparisonMetric,
    ComparisonSource,
    DatasetAssumption,
    ExecutionObservation,
    FillObservation,
    MarketRequirements,
    ReconciliationTolerances,
    RunObservations,
    RuntimeObservation,
    RuntimeRequirements,
    StrategyLifecycleStage,
    StrategyProgression,
    StrategyVersionRef,
    SymbolMapping,
    Tolerance,
    TradeObservation,
    advance_progression,
    begin_progression,
    build_specification,
    compare_expected_paper_live,
    compare_runs,
    evaluate_health,
    illegal_progression_move,
    pause_progression,
    reconcile_execution_state,
    resume_progression,
    specification_id_for,
    validate_specification,
)
from alphalab.portfolio.account import Account
from alphalab.portfolio.amounts import CurrencyAmounts
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
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineState,
)
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

# --------------------------------------------------------------------------- #
# Shared declarations
# --------------------------------------------------------------------------- #

LIMITS = RiskLimits(
    order_size=OrderSizeLimit(Decimal("100"), Decimal("10000")),
    position=PositionLimit(Decimal("1000"), Decimal("100000")),
    exposure=ExposureLimit(Decimal("200000"), Decimal("150000")),
    leverage=LeverageLimit(Decimal("2")),
    margin=MarginLimit(Decimal("0.5")),
    daily_loss=DailyLossLimit(Decimal("5000")),
    drawdown=DrawdownLimit(Decimal("0.2")),
)

CAPITAL = CapitalPolicy("acct-bench", "USD", Decimal("1000000"), ("USD",))

BROKER_REQUIREMENTS = BrokerRequirements(
    order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}),
    time_in_force=frozenset({TimeInForce.DAY}),
    asset_classes=frozenset({AssetType.EQUITY}),
    short_selling=True,
    fractional_quantities=False,
)

MARKET_REQUIREMENTS = MarketRequirements(
    instruments=tuple(f"a-{index:04d}" for index in range(50)),
    venues=("XNYS", "XNAS"),
    calendar_ids=("XNYS", "XNAS"),
    quote_currencies=("USD",),
)

RUNTIME_REQUIREMENTS = RuntimeRequirements(
    max_data_staleness_seconds=60.0,
    max_heartbeat_silence_seconds=30.0,
    max_execution_latency_seconds=2.0,
    position_tolerance=Tolerance(absolute=Decimal("0.000001")),
)

SPECIFICATION = build_specification(
    strategy=StrategyVersionRef("bench", 1),
    parameters={f"p{index}": float(index) for index in range(20)},
    datasets=(DatasetAssumption("prices", "alphalab.dataset.v1:bench"),),
    risk=LIMITS,
    capital=CAPITAL,
    broker=BROKER_REQUIREMENTS,
    market=MARKET_REQUIREMENTS,
    runtime=RUNTIME_REQUIREMENTS,
)

COMPARISON_TOLERANCES = {
    ComparisonMetric.TRADE_QUANTITY: Tolerance(absolute=Decimal("0")),
    ComparisonMetric.TRADE_PRICE: Tolerance(absolute=Decimal("0.01")),
    ComparisonMetric.FILL_QUANTITY: Tolerance(absolute=Decimal("0")),
    ComparisonMetric.FILL_PRICE: Tolerance(absolute=Decimal("0.01")),
    ComparisonMetric.SLIPPAGE: Tolerance(absolute=Decimal("0.005")),
    ComparisonMetric.EXECUTION_LATENCY: Tolerance(absolute=Decimal("0.25")),
    ComparisonMetric.REALIZED_PNL: Tolerance(absolute=Decimal("0.01")),
    ComparisonMetric.EXPOSURE: Tolerance(relative=Decimal("0.001")),
}

RECONCILIATION_TOLERANCES = ReconciliationTolerances(
    order_quantity=Tolerance(absolute=Decimal("0")),
    order_price=Tolerance(absolute=Decimal("0.01")),
    fill_quantity=Tolerance(absolute=Decimal("0")),
    fill_price=Tolerance(absolute=Decimal("0.01")),
    commission=Tolerance(absolute=Decimal("0.01")),
    position_quantity=Tolerance(absolute=Decimal("0.000001")),
    cash=Tolerance(absolute=Decimal("1000000")),
)


def _timed(label: str, count: int, work: Callable[[], object]) -> None:
    start = time.perf_counter()
    work()
    duration = time.perf_counter() - start
    print(f"  {label:<46} {duration:.4f}s, {count / max(duration, 1e-9):>12,.0f} ops/sec")


# --------------------------------------------------------------------------- #
# 1. Lifecycle transitions
# --------------------------------------------------------------------------- #


def benchmark_progression() -> None:
    print("\n[1] Strategy lifecycle transitions")

    for count in (10_000, 40_000):

        def walk(steps: int = count) -> object:
            progression = begin_progression(StrategyVersionRef("bench", 1))
            progression = advance_progression(
                progression, StrategyLifecycleStage.BACKTEST, "start", 0.0
            )
            for index in range(steps):
                target = (
                    StrategyLifecycleStage.VALIDATION
                    if index % 2 == 0
                    else StrategyLifecycleStage.BACKTEST
                )
                progression = advance_progression(progression, target, "step", float(index))
            return progression

        _timed(f"advance x{count:,} (history grows to {count:,})", count, walk)

    # The refusal path: a caller asking "would this be allowed?" before acting.
    progression = begin_progression(StrategyVersionRef("bench", 1))
    checks = 200_000
    _timed(
        f"illegal_progression_move x{checks:,}",
        checks,
        lambda: [
            illegal_progression_move(progression, StrategyLifecycleStage.LIVE)
            for _ in range(checks)
        ],
    )

    # A year of daily pauses and resumes on a live strategy: the resume target
    # is resolved from the history each time.
    cycles = 50_000
    live = begin_progression(StrategyVersionRef("bench", 2))
    for index, stage in enumerate(
        (
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.PAPER,
            StrategyLifecycleStage.PRODUCTION_CANDIDATE,
            StrategyLifecycleStage.LIVE,
        )
    ):
        live = advance_progression(live, stage, "promote", float(index))

    def pause_and_resume(state: StrategyProgression = live) -> StrategyProgression:
        current: StrategyProgression = state
        for index in range(cycles):
            current = pause_progression(current, "halt", float(index))
            current = resume_progression(current, "resume", float(index))
        return current

    _timed(f"pause + resume x{cycles:,}", cycles * 2, pause_and_resume)


# --------------------------------------------------------------------------- #
# 2. Deployment specifications
# --------------------------------------------------------------------------- #


def benchmark_specifications() -> None:
    print("\n[2] Deployment specifications")

    for count in (20_000, 80_000):
        _timed(
            f"specification_id_for x{count:,} (20 parameters)",
            count,
            lambda n=count: [  # type: ignore[misc]
                specification_id_for(
                    SPECIFICATION.strategy,
                    SPECIFICATION.parameters,
                    SPECIFICATION.datasets,
                    SPECIFICATION.risk,
                    SPECIFICATION.capital,
                    SPECIFICATION.broker,
                    SPECIFICATION.market,
                    SPECIFICATION.runtime,
                )
                for _ in range(n)
            ],
        )

    count = 20_000
    _timed(
        f"validate_specification x{count:,}",
        count,
        lambda: [validate_specification(SPECIFICATION) for _ in range(count)],
    )

    versions = 5_000
    _timed(
        f"build_specification x{versions:,} (a book of strategies)",
        versions,
        lambda: [
            build_specification(
                strategy=StrategyVersionRef(f"line-{index:05d}", 1),
                parameters={f"p{step}": float(step) for step in range(20)},
                datasets=(DatasetAssumption("prices", f"dsv-{index:05d}"),),
                risk=LIMITS,
                capital=CAPITAL,
                broker=BROKER_REQUIREMENTS,
                market=MARKET_REQUIREMENTS,
                runtime=RUNTIME_REQUIREMENTS,
            )
            for index in range(versions)
        ],
    )


# --------------------------------------------------------------------------- #
# 3. Runtime health
# --------------------------------------------------------------------------- #


def _observation(count: int) -> RuntimeObservation:
    return RuntimeObservation(
        observed_at=1000.0,
        last_market_data_at=999.0,
        last_heartbeat_at=999.5,
        broker_connection=ConnectionStatus.CONNECTED,
        executions=tuple(
            ExecutionObservation(f"o-{index:06d}", 1.0, 1.5) for index in range(count)
        ),
        observed_positions={f"a-{index:06d}": Decimal("100") for index in range(count)},
        expected_positions={f"a-{index:06d}": Decimal("100") for index in range(count)},
        risk_violations=(),
        state_expectations=(),
    )


def benchmark_health() -> None:
    print("\n[3] Runtime health evaluation")

    for count in (25_000, 100_000):
        observation = _observation(count)
        _timed(
            f"evaluate_health over {count:,} executions + positions",
            count,
            lambda o=observation: evaluate_health(SPECIFICATION, o),  # type: ignore[misc]
        )

    # The all-clean case above is the cheap one; this is the one that builds a
    # finding per record, which is what an incident actually looks like.
    breached = _observation(50_000)
    breaking = RuntimeObservation(
        observed_at=breached.observed_at,
        last_market_data_at=0.0,
        last_heartbeat_at=0.0,
        broker_connection=ConnectionStatus.FAILED,
        executions=breached.executions,
        observed_positions={asset: Decimal("0") for asset in (breached.observed_positions or {})},
        expected_positions=breached.expected_positions,
        risk_violations=(),
        state_expectations=(),
    )
    _timed(
        "evaluate_health with 50,000 position breaches",
        50_000,
        lambda: evaluate_health(SPECIFICATION, breaking),
    )


# --------------------------------------------------------------------------- #
# 4. Expected / paper / live comparison
# --------------------------------------------------------------------------- #


def _observations(source: ComparisonSource, count: int, drift: str = "0") -> RunObservations:
    keys = [f"k-{index:06d}" for index in range(count)]
    offset = Decimal(drift)
    return RunObservations(
        source=source,
        trades=tuple(
            TradeObservation(
                key, f"a-{index % 50:04d}", Side.BUY, Decimal("10"), Decimal("100") + offset, 1.0
            )
            for index, key in enumerate(keys)
        ),
        fills=tuple(
            FillObservation(
                key,
                f"a-{index % 50:04d}",
                Side.BUY,
                Decimal("10"),
                Decimal("100") + offset,
                Decimal("1"),
                1.0,
                slippage=Decimal("0.002"),
                latency_seconds=Decimal("0.1"),
            )
            for index, key in enumerate(keys)
        ),
        realized_pnl=CurrencyAmounts.single(Decimal("1250.00"), "USD"),
        exposure=CurrencyAmounts.single(Decimal("500000.00"), "USD"),
    )


def benchmark_comparison() -> None:
    print("\n[4] Expected / paper / live comparison")

    for count in (10_000, 40_000):
        expected = _observations(ComparisonSource.EXPECTED, count)
        paper = _observations(ComparisonSource.PAPER, count)
        _timed(
            f"expected vs paper, {count:,} trades + {count:,} fills",
            count,
            lambda left=expected, right=paper: compare_runs(  # type: ignore[misc]
                left, right, COMPARISON_TOLERANCES
            ),
        )

    count = 10_000
    expected = _observations(ComparisonSource.EXPECTED, count)
    live = _observations(ComparisonSource.LIVE, count, drift="0.03")
    _timed(
        f"expected vs live, {count:,} records, every fill divergent",
        count,
        lambda: compare_runs(expected, live, COMPARISON_TOLERANCES),
    )

    paper = _observations(ComparisonSource.PAPER, count)
    _timed(
        f"three-way over {count:,} records",
        count * 3,
        lambda: compare_expected_paper_live(expected, paper, live, COMPARISON_TOLERANCES),
    )


# --------------------------------------------------------------------------- #
# 5. Reconciliation
# --------------------------------------------------------------------------- #


def _pipeline() -> ExecutionPipelineState:
    cash = Decimal("1000000")
    config = ExecutionPipelineConfig(
        account=Account("acct-bench", "USD", "Benchmark", 1.0),
        starting_cash=cash,
        budget=CapitalBudget(
            global_capital=cash,
            maximum_exposure=cash * Decimal("10"),
            cash_buffer=Decimal("0"),
            strategy_budgets={"bench": cash},
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True, enforce_integer_quantities=False
        ),
        risk_limits=LIMITS,
    )
    return ExecutionPipeline.initialize(config, StrategyRuntimeState(), 1.0)


def _broker(count: int) -> tuple[BrokerState, ExternalOrderMap]:
    mapping = ExternalOrderMap()
    orders: dict[str, BrokerOrder] = {}
    executions: dict[str, BrokerExecution] = {}
    positions: dict[str, BrokerPosition] = {}
    for index in range(count):
        oms_id = f"oms-{index:06d}"
        broker_id = f"ALB-{oms_id}"
        symbol = f"a-{index:06d}"
        mapping = mapping.bind(oms_id, broker_id)
        orders[broker_id] = BrokerOrder(
            broker_order_id=broker_id,
            oms_order_id=oms_id,
            symbol=symbol,
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("10"),
            price=Decimal("100"),
            filled_quantity=Decimal("10"),
            average_fill_price=Decimal("100"),
            status=OrderStatus.FILLED,
            created_at=1.0,
            updated_at=2.0,
        )
        executions[f"x-{index:06d}"] = BrokerExecution(
            execution_id=f"x-{index:06d}",
            broker_order_id=broker_id,
            symbol=symbol,
            fill_quantity=Decimal("10"),
            fill_price=Decimal("100"),
            commission=Decimal("1"),
            timestamp=2.0,
        )
        positions[symbol] = BrokerPosition(
            symbol, Decimal("0"), Decimal("100"), Decimal("0"), Decimal("0"), Decimal("0")
        )
    state = BrokerState(
        broker_name="bench",
        connection_status=ConnectionStatus.CONNECTED,
        account=BrokerAccount(
            "acct-bench",
            Decimal("1000000"),
            Decimal("1000000"),
            Decimal("1000000"),
            Decimal("0"),
            Decimal("1000000"),
            "USD",
        ),
        positions=PersistentMap(positions),
        orders=PersistentMap(orders),
        executions=PersistentMap(executions),
    )
    return state, mapping


def benchmark_reconciliation() -> None:
    print("\n[5] AlphaLab-to-broker reconciliation")

    pipeline = _pipeline()
    symbols = SymbolMapping.identity()

    for count in (5_000, 20_000):
        broker, mapping = _broker(count)
        _timed(
            f"reconcile {count:,} orders + {count:,} fills + {count:,} positions",
            count * 3,
            lambda b=broker, m=mapping: reconcile_execution_state(  # type: ignore[misc]
                pipeline, b, m, symbols, RECONCILIATION_TOLERANCES
            ),
        )

    # Repeated reconciliation over an unchanged pair: the stability property a
    # caller relies on when it stores one report and diffs the next against it.
    broker, mapping = _broker(5_000)
    repeats = 20
    _timed(
        f"reconcile the same 5,000-order state x{repeats}",
        repeats,
        lambda: [
            reconcile_execution_state(pipeline, broker, mapping, symbols, RECONCILIATION_TOLERANCES)
            for _ in range(repeats)
        ],
    )


def run_benchmark() -> None:
    print("=" * 78)
    print("AlphaLab v3.5 -- strategy execution and production intelligence")
    print("=" * 78)
    benchmark_progression()
    benchmark_specifications()
    benchmark_health()
    benchmark_comparison()
    benchmark_reconciliation()
    print()


if __name__ == "__main__":
    run_benchmark()
