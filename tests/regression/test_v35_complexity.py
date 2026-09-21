"""The v3.5 paths stay near-linear, measured on every run.

Same shape as ``test_v34_complexity.py``: assert on the **growth ratio** between
two input sizes rather than on an absolute duration, so the test is about the
algorithm rather than the machine. Bounds sit well above linear and well below
quadratic, because the point is to catch a change of complexity class and a
tight bound on shared hardware is a flaky test.

Which paths, and why these
---------------------------

Each of the four is one where the obvious implementation is quadratic:

* **Health evaluation** walks executions and the union of two position
  mappings. A membership test against a list rather than a mapping would make
  either quadratic.
* **Comparison** aligns two sets of trades and fills by key. Scanning the other
  side per record is the natural mistake, and it is the one that would go
  unnoticed on the six-fill fixtures the unit tests use.
* **Reconciliation** does the same across four indexes at once.
* **A progression's history** is an ``AppendOnlyLog``, so appending to it shares
  structure instead of copying; rebuilding a tuple per transition would make a
  long-lived strategy's history quadratic in its own length.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal

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
    compare_runs,
    evaluate_health,
    pause_progression,
    reconcile_execution_state,
    resume_progression,
    resume_target,
)
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
from tests.unit.lifecycle.test_reconciliation import base_pipeline

#: A ratio above linear and well below quadratic. Quadrupling the input
#: quadruples a linear path and multiplies a quadratic one by sixteen.
LINEAR_BOUND = 8.0


def _elapsed(work: Callable[[], object]) -> float:
    """Best of three, so one scheduling hiccup does not fail the suite."""

    return min(_once(work) for _ in range(3))


def _once(work: Callable[[], object]) -> float:
    start = time.perf_counter()
    work()
    return time.perf_counter() - start


def _growth(small: Callable[[], object], large: Callable[[], object]) -> float:
    return _elapsed(large) / max(_elapsed(small), 1e-4)


# --------------------------------------------------------------------------- #
# Shared fixtures
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

SPEC = build_specification(
    strategy=StrategyVersionRef("bench", 1),
    parameters={"a": 1.0},
    datasets=(DatasetAssumption("prices", "dsv"),),
    risk=LIMITS,
    capital=CapitalPolicy("acct", "USD", Decimal("1000000"), ("USD",)),
    broker=BrokerRequirements(
        order_types=frozenset({OrderType.MARKET}),
        time_in_force=frozenset({TimeInForce.DAY}),
        asset_classes=frozenset({AssetType.EQUITY}),
        short_selling=False,
        fractional_quantities=False,
    ),
    market=MarketRequirements(("a",), ("XNYS",), ("XNYS",), ("USD",)),
    runtime=RuntimeRequirements(60.0, 30.0, 2.0, Tolerance(absolute=Decimal("0.5"))),
)

TOLERANCES = dict.fromkeys(ComparisonMetric, Tolerance(absolute=Decimal("0.01")))

RECONCILIATION_TOLERANCES = ReconciliationTolerances(
    order_quantity=Tolerance(absolute=Decimal("0")),
    order_price=Tolerance(absolute=Decimal("0.01")),
    fill_quantity=Tolerance(absolute=Decimal("0")),
    fill_price=Tolerance(absolute=Decimal("0.01")),
    commission=Tolerance(absolute=Decimal("0.01")),
    position_quantity=Tolerance(absolute=Decimal("0")),
    cash=Tolerance(absolute=Decimal("1000000")),
)


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


def _observation(count: int) -> RuntimeObservation:
    return RuntimeObservation(
        observed_at=1000.0,
        last_market_data_at=999.0,
        last_heartbeat_at=999.0,
        broker_connection=ConnectionStatus.CONNECTED,
        executions=tuple(
            ExecutionObservation(f"o-{index:06d}", 1.0, 1.5) for index in range(count)
        ),
        observed_positions={f"a-{index:06d}": Decimal("1") for index in range(count)},
        expected_positions={f"a-{index:06d}": Decimal("1") for index in range(count)},
        risk_violations=(),
        state_expectations=(),
    )


def test_health_evaluation_is_near_linear_in_what_was_observed() -> None:
    small, large = _observation(2_000), _observation(8_000)

    ratio = _growth(lambda: evaluate_health(SPEC, small), lambda: evaluate_health(SPEC, large))

    assert ratio < LINEAR_BOUND, f"health evaluation grew {ratio:.1f}x for 4x the observations"


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


def _observations(source: ComparisonSource, count: int) -> RunObservations:
    keys = [f"k-{index:06d}" for index in range(count)]
    return RunObservations(
        source=source,
        trades=tuple(
            TradeObservation(key, "a", Side.BUY, Decimal("1"), Decimal("100"), 1.0) for key in keys
        ),
        fills=tuple(
            FillObservation(
                key,
                "a",
                Side.BUY,
                Decimal("1"),
                Decimal("100"),
                Decimal("0"),
                1.0,
                slippage=Decimal("0"),
                latency_seconds=Decimal("0.1"),
            )
            for key in keys
        ),
    )


def test_comparing_two_runs_is_near_linear_in_their_records() -> None:
    def work(count: int) -> Callable[[], object]:
        left = _observations(ComparisonSource.EXPECTED, count)
        right = _observations(ComparisonSource.LIVE, count)
        return lambda: compare_runs(left, right, TOLERANCES)

    ratio = _growth(work(1_000), work(4_000))

    assert ratio < LINEAR_BOUND, f"comparison grew {ratio:.1f}x for 4x the records"


def test_comparing_two_runs_that_share_no_keys_is_still_near_linear() -> None:
    """The worst case for alignment: every key is missing on the other side."""

    def work(count: int) -> Callable[[], object]:
        left = _observations(ComparisonSource.EXPECTED, count)
        right = RunObservations(
            source=ComparisonSource.LIVE,
            trades=tuple(
                TradeObservation(
                    f"other-{index:06d}", "a", Side.BUY, Decimal("1"), Decimal("100"), 1.0
                )
                for index in range(count)
            ),
            fills=(),
        )
        return lambda: compare_runs(left, right, TOLERANCES)

    ratio = _growth(work(1_000), work(4_000))

    assert ratio < LINEAR_BOUND, f"a disjoint comparison grew {ratio:.1f}x for 4x the records"


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #


def _broker_state(count: int) -> tuple[BrokerState, ExternalOrderMap]:
    orders = []
    executions = []
    positions = []
    mapping = ExternalOrderMap()
    for index in range(count):
        oms_id = f"oms-{index:06d}"
        broker_id = f"ALB-{oms_id}"
        mapping = mapping.bind(oms_id, broker_id)
        orders.append(
            BrokerOrder(
                broker_order_id=broker_id,
                oms_order_id=oms_id,
                symbol=f"a-{index:06d}",
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                quantity=Decimal("1"),
                price=Decimal("100"),
                filled_quantity=Decimal("0"),
                average_fill_price=Decimal("0"),
                status=OrderStatus.ACCEPTED,
                created_at=1.0,
                updated_at=1.0,
            )
        )
        executions.append(
            BrokerExecution(
                execution_id=f"x-{index:06d}",
                broker_order_id=broker_id,
                symbol=f"a-{index:06d}",
                fill_quantity=Decimal("1"),
                fill_price=Decimal("100"),
                commission=Decimal("0"),
                timestamp=2.0,
            )
        )
        positions.append(
            BrokerPosition(
                f"a-{index:06d}",
                Decimal("0"),
                Decimal("100"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
            )
        )
    state = BrokerState(
        broker_name="bench",
        connection_status=ConnectionStatus.CONNECTED,
        account=BrokerAccount(
            "acct",
            Decimal("100000"),
            Decimal("100000"),
            Decimal("100000"),
            Decimal("0"),
            Decimal("100000"),
            "USD",
        ),
        positions=PersistentMap({position.symbol: position for position in positions}),
        orders=PersistentMap({order.broker_order_id: order for order in orders}),
        executions=PersistentMap({execution.execution_id: execution for execution in executions}),
    )
    return state, mapping


def test_reconciliation_is_near_linear_in_the_state_it_compares() -> None:
    def work(count: int) -> Callable[[], object]:
        pipeline = base_pipeline()
        broker, mapping = _broker_state(count)
        return lambda: reconcile_execution_state(
            pipeline, broker, mapping, SymbolMapping.identity(), RECONCILIATION_TOLERANCES
        )

    ratio = _growth(work(500), work(2_000))

    assert ratio < LINEAR_BOUND, f"reconciliation grew {ratio:.1f}x for 4x the state"


# --------------------------------------------------------------------------- #
# The progression's history
# --------------------------------------------------------------------------- #


def _walk(steps: int) -> Callable[[], object]:
    """Alternate between two legal stages, so the history grows without end."""

    def work() -> object:
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

    return work


def test_a_long_progression_history_costs_linear_time_to_build() -> None:
    ratio = _growth(_walk(2_000), _walk(8_000))

    assert ratio < LINEAR_BOUND, f"building a history grew {ratio:.1f}x for 4x the transitions"


def test_reading_a_resume_target_does_not_rescan_a_long_history() -> None:
    """The scan walks backwards **by index**, so a pause at the end of a long
    history costs the same as a pause at the end of a short one.

    The bound here is tighter than :data:`LINEAR_BOUND` on purpose: a linear
    implementation -- one that materializes the log before reversing it, which
    is what ``StrategyProgression.transitions`` does -- gives a ratio near four
    at these sizes and would pass the loose bound while being the defect this
    test exists to catch.
    """

    reads = 20_000

    def paused(steps: int) -> Callable[[], object]:
        progression = _to_paper(steps)
        progression = advance_progression(progression, StrategyLifecycleStage.PAUSED, "hold", 3.0)
        # Many reads per measurement: one is far below the timer's resolution,
        # and a ratio taken against a clamped denominator measures nothing.
        return lambda: [resume_target(progression) for _ in range(reads)]

    ratio = _growth(paused(2_000), paused(16_000))

    assert ratio < 3.0, f"resolving a resume target grew {ratio:.1f}x for 8x the history"


def _to_paper(steps: int) -> StrategyProgression:
    """A progression with ``steps`` transitions behind it, sitting at ``PAPER``."""

    progression = begin_progression(StrategyVersionRef("bench", 1))
    progression = advance_progression(progression, StrategyLifecycleStage.BACKTEST, "start", 0.0)
    for index in range(steps):
        target = (
            StrategyLifecycleStage.VALIDATION if index % 2 == 0 else StrategyLifecycleStage.BACKTEST
        )
        progression = advance_progression(progression, target, "step", float(index))
    if progression.stage is StrategyLifecycleStage.BACKTEST:
        progression = advance_progression(
            progression, StrategyLifecycleStage.VALIDATION, "to paper", 1.0
        )
    return advance_progression(progression, StrategyLifecycleStage.PAPER, "to paper", 2.0)


def test_pausing_and_resuming_daily_stays_linear_in_the_cycles() -> None:
    """The defect ``benchmarks/benchmark_strategy_execution.py`` found: a
    ``resume_target`` that materialized the history made a strategy which
    pauses every day cost time quadratic in its own history -- 50,000 cycles
    took 23 seconds, and the same loop takes a fifth of a second now."""

    def cycles(count: int) -> Callable[[], object]:
        base = _to_paper(10)

        def work() -> object:
            progression = base
            for index in range(count):
                progression = pause_progression(progression, "halt", float(index))
                progression = resume_progression(progression, "resume", float(index))
            return progression

        return work

    ratio = _growth(cycles(4_000), cycles(16_000))

    assert ratio < LINEAR_BOUND, f"pause/resume grew {ratio:.1f}x for 4x the cycles"
