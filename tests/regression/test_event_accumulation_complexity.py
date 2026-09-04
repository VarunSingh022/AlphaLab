"""Regression guard for the O(N^2) event accumulation fixed in v2.1.

Before v2.1 every engine grew its append-only history with
``(*state.events, event)``, rebuilding the whole tuple on each transition. A run
of N transitions therefore copied O(N^2) elements, and
``benchmarks/benchmark_risk_engine.py`` could not finish its 100k-evaluation
workload inside 30 seconds. :class:`~alphalab.common.append_log.AppendOnlyLog`
makes those appends O(1) amortized.

The structural tests below are deterministic: they assert the log does not copy
its history, which is the property the fix rests on. The timing test is a
coarse backstop with a wide tolerance -- it is there to catch a return to
quadratic behaviour (which would be ~64x over an 8x workload increase), not to
police constant factors.
"""

import time
from dataclasses import replace
from decimal import Decimal

from alphalab.common.append_log import AppendOnlyLog
from alphalab.core.enums import Side
from alphalab.core.order_request import OrderRequest
from alphalab.execution.engine import ExecutionEngine
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.state import ExecutionState
from alphalab.market.engine import MarketEngine
from alphalab.market.quote import Quote
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.risk.engine import RiskEngine
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
from alphalab.risk.state import RiskState

# Ratio of the two workload sizes used by the timing test.
SCALE = 8
SMALL = 2_000
LARGE = SMALL * SCALE
# Linear growth predicts ~8x. Quadratic growth predicts ~64x. Anything under 24x
# (3x the linear prediction) is comfortably not quadratic.
MAX_GROWTH = 24.0
# 20k risk evaluations took ~4.7s before the fix and ~0.3s after, on the machine
# this was developed on. A 15s ceiling survives a slow CI runner while still
# failing a reintroduced quadratic.
LARGE_WORKLOAD_BUDGET_SECONDS = 15.0


def _limits() -> RiskLimits:
    return RiskLimits(
        order_size=OrderSizeLimit(Decimal("1000"), Decimal("100000")),
        position=PositionLimit(Decimal("5000"), Decimal("500000")),
        exposure=ExposureLimit(Decimal("1000000"), Decimal("500000")),
        leverage=LeverageLimit(Decimal("2.0")),
        margin=MarginLimit(Decimal("0.80")),
        daily_loss=DailyLossLimit(Decimal("10000")),
        drawdown=DrawdownLimit(Decimal("0.10")),
    )


def _funded_risk_state() -> RiskState:
    return replace(RiskEngine.reset(_limits()), buying_power=Decimal("1000000"))


def _request() -> OrderRequest:
    return OrderRequest(
        order_id="BENCH-ORD",
        strategy_id="STRAT-01",
        asset_id="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("150.00"),
    )


def _time_risk_evaluations(count: int) -> float:
    state = _funded_risk_state()
    request = _request()
    start = time.perf_counter()
    for i in range(count):
        state, _ = RiskEngine.evaluate(state, request, float(i))
    return time.perf_counter() - start


# ---------------------------------------------------------------------------
# Structural guarantees (deterministic)
# ---------------------------------------------------------------------------


def test_risk_state_histories_are_append_only_logs() -> None:
    state = _funded_risk_state()

    assert isinstance(state.events, AppendOnlyLog)
    assert isinstance(state.history, AppendOnlyLog)


def test_risk_evaluation_does_not_copy_its_history() -> None:
    """Each evaluation appends in place; the history buffer is never rebuilt."""

    state = _funded_risk_state()
    request = _request()
    buffers = set()
    for i in range(200):
        state, _ = RiskEngine.evaluate(state, request, float(i))
        buffers.add(id(state.history._buffer))
        buffers.add(id(state.events._buffer))

    # One buffer for `history`, one for `events`, for all 200 evaluations.
    assert len(buffers) == 2
    assert len(state.history) == 200
    assert len(state.events) == 400  # a check-started plus a decision event each


def test_risk_evaluation_retains_the_full_history() -> None:
    """The fix must not silently drop history to go faster."""

    state = _funded_risk_state()
    request = _request()
    for i in range(500):
        state, _ = RiskEngine.evaluate(state, request, float(i))

    assert len(state.history) == 500
    assert [d.timestamp for d in state.history[:3]] == [0.0, 1.0, 2.0]
    assert state.history[-1].timestamp == 499.0
    assert all(d.approved for d in state.history)


def test_other_execution_path_engines_also_append_without_copying() -> None:
    """Market, execution and portfolio share the same append-only guarantee."""

    market = MarketEngine.reset()
    market_buffers = set()
    for i in range(100):
        market = MarketEngine.publish_quote(
            market,
            Quote(
                "AAPL",
                float(i) + 1.0,
                Decimal("99"),
                Decimal("101"),
                Decimal("1"),
                Decimal("1"),
                "SIM",
                "USD",
            ),
        )
        market_buffers.add(id(market.events._buffer))
    assert len(market_buffers) == 1
    assert len(market.events) == 100

    execution = ExecutionState()
    execution_buffers = set()
    instruction = OrderInstruction(
        "ORD-1", "STRAT", "AAPL", Decimal("1"), Decimal("100"), Side.BUY, "SIM", "USD"
    )
    for i in range(100):
        execution = ExecutionEngine.simulate(
            execution,
            ExecutionSimulator(),
            instruction,
            Decimal("1"),
            Decimal("100"),
            float(i) + 1.0,
            FillStatus.FULL_FILL,
        )
        execution_buffers.add(id(execution.history._buffer))
    assert len(execution_buffers) == 1
    assert len(execution.history) == 100

    portfolio = PortfolioEngine.apply_deposit(
        PortfolioState(account=Account("ACC", "USD", "Perf", 0.0)),
        Decimal("1000000"),
        "USD",
        0.0,
    )
    portfolio_buffers = set()
    for i in range(100):
        portfolio = PortfolioEngine.apply_fill(
            portfolio, "AAPL", Decimal("1"), Decimal("100.00"), Decimal("0.00"), float(i) + 1.0
        )
        portfolio_buffers.add(id(portfolio.events._buffer))
    assert len(portfolio_buffers) == 1
    assert len(portfolio.ledger.history()) == 101  # the deposit plus 100 fills


# ---------------------------------------------------------------------------
# Timing backstop (coarse)
# ---------------------------------------------------------------------------


def test_risk_evaluation_cost_grows_linearly_with_the_workload() -> None:
    # Warm up so import-time and first-call costs do not skew the small sample.
    _time_risk_evaluations(200)

    small = min(_time_risk_evaluations(SMALL) for _ in range(2))
    large = min(_time_risk_evaluations(LARGE) for _ in range(2))

    assert large < LARGE_WORKLOAD_BUDGET_SECONDS, f"{LARGE} risk evaluations took {large:.2f}s"
    growth = large / max(small, 1e-6)
    assert growth < MAX_GROWTH, (
        f"{SCALE}x the workload cost {growth:.1f}x the time "
        f"({small:.3f}s -> {large:.3f}s); event accumulation looks quadratic again"
    )
