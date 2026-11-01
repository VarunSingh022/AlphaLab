"""
AlphaLab Examples
=================

Example 74 : Evaluate an Execution Algorithm in a Backtest

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 63 (execution algorithms)
✓ Example 65 (execution analytics)
✓ Example 70 (build a strategy)

Topics
------

• One decision -- buy 3,000 shares -- executed five ways over the same
  historical minute bars: whole, and by TWAP, VWAP, participation and iceberg
• Each algorithm's children released at the bars, filled by the run's own
  liquidity cap and cost model, and settled on one parent OMS order
• What each run paid: spread, square-root impact, commission per fill, and the
  quantity it never filled, measured as implementation shortfall
• The replay equal to the backtest, and a replay paused with a child working,
  written to JSON and resumed to the same result

What this shows
---------------

v4.1.1's evidence for ledger FUT-009: how a strategy's orders are executed is
part of what a backtest measures. A strategy decides *what* to own; an
:class:`~alphalab.backtesting.ExecutionPlan` decides how its orders are worked,
and every child goes through the canonical path -- the fill policy, the cost
model, the OMS, the portfolio, the allocation reservation -- so the comparison
below is between execution choices, not between engines. The bars are
**synthetic** and the VWAP profile is a stated shape, not a forecast.
``tests/integration/test_algorithms_on_the_historical_path.py`` holds the
mechanism to its properties.

Run

    python examples/74_evaluate_an_execution_algorithm.py
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import (
    AlgorithmAssignment,
    AlgorithmicResult,
    BacktestEngine,
    BacktestResult,
    ExecutionMode,
    ExecutionPlan,
    MarketDataset,
    PausedReplay,
    ReplayBacktest,
    RunConfig,
    RunSchedule,
)
from alphalab.backtesting.algorithmic import capture_book
from alphalab.backtesting.replay_snapshot import capture, from_primitives, restore
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import AssetType, OrderStatus
from alphalab.core.order_request import OrderRequest
from alphalab.execution.algorithms import (
    TWAP,
    VWAP,
    ExecutionAlgorithm,
    Iceberg,
    IncompletePolicy,
    IntervalVolume,
    MissingVolumePolicy,
    Participation,
    Urgency,
    VolumeProfile,
)
from alphalab.execution.commission import FixedCommission
from alphalab.execution.costs import (
    ExecutionCostModel,
    FixedHalfSpread,
    NoFee,
    NoSlippage,
    NoTax,
    SquareRootImpact,
)
from alphalab.execution.policy import FillTiming, LiquidityCappedFill
from alphalab.execution.quality import (
    ExecutionBenchmarks,
    OrderExecution,
    implementation_shortfall,
)
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import digest_run
from alphalab.market.bar import Bar, TimeFrame
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.account import Account
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
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy import DiscardingLogger, FixedClock, context_factory
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

# --------------------------------------------------------------------------- #
# 1. The market: one hour of minute bars, a U-shaped day
# --------------------------------------------------------------------------- #

STOCK = InstrumentRecord("WIDGET", AssetType.EQUITY, "XNAS", "USD")
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), (STOCK,))
OPEN = 1_709_562_600.0  # 2024-03-04 09:30 New York
MINUTE = 60.0
MINUTES = 60
CENT = Decimal("0.01")


def at(minute: int) -> float:
    return OPEN + minute * MINUTE


def price(minute: int) -> Decimal:
    """A drifting, wavering price: up 30 cents over the hour."""

    level = 50 + 0.005 * minute + 0.06 * math.sin(minute / 4)
    return Decimal(str(level)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def volume(minute: int) -> Decimal:
    """Heavy at the open, light at midday: 4,000 shares falling to 1,500."""

    return Decimal(1500 + int(2500 * ((minute - MINUTES) / MINUTES) ** 2))


def dataset() -> MarketDataset:
    bars = []
    for minute in range(MINUTES):
        close = price(minute)
        bars.append(
            Bar(
                STOCK.asset_id,
                at(minute),
                close,
                close + Decimal("0.03"),
                close - Decimal("0.03"),
                close,
                volume(minute),
                None,
                None,
                TimeFrame.M1,
            )
        )
    return MarketDataset.of("EX74-MINUTES", bars)


# --------------------------------------------------------------------------- #
# 2. The decision, and the execution choices
# --------------------------------------------------------------------------- #

STRATEGY_ID = "EX74-BUYER"
QUANTITY = Decimal("3000")


class Buyer(BaseStrategy):
    """Buys 3,000 shares at the open. What it owns is decided; how is the plan's."""

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.bar.timestamp != OPEN:
            return ()
        return (Intent(STRATEGY_ID, STOCK.asset_id, QUANTITY, timestamp=OPEN),)


def running() -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, Buyer())
    strategy = state.strategies[STRATEGY_ID]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(strategy, frozenset({"bars"}), 1.2)
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return replace(state, strategies={STRATEGY_ID: strategy})


WINDOW = 30 * MINUTE


def profile() -> VolumeProfile:
    """The volume the desk *expects*, six five-minute buckets: a stated shape."""

    weights = (6, 5, 4, 3, 3, 3)
    return VolumeProfile(
        tuple(IntervalVolume(at(5 * n), at(5 * n + 5), Decimal(w)) for n, w in enumerate(weights)),
        "desk's U-shape for the first half hour (stated, not estimated)",
    )


CHOICES: dict[str, ExecutionAlgorithm | None] = {
    "whole": None,
    "twap": TWAP(6, Urgency.neutral()),
    "vwap": VWAP(profile(), MissingVolumePolicy.REFUSE, Urgency.neutral()),
    "participation": Participation(Decimal("0.10"), None, None, IncompletePolicy.LEAVE_UNFILLED),
    "iceberg": Iceberg(Decimal("400")),
}


def plan(algorithm: ExecutionAlgorithm | None) -> ExecutionPlan | None:
    if algorithm is None:
        return None
    return ExecutionPlan(AlgorithmAssignment(algorithm, WINDOW, Decimal("1")))


# --------------------------------------------------------------------------- #
# 3. The run: a liquidity cap and a stated cost model
# --------------------------------------------------------------------------- #

#: Every cost stated: a cent of half-spread, square-root impact on the share of
#: the bar's volume a fill takes, and a dollar per fill.
COSTS = ExecutionCostModel(
    spread_model=FixedHalfSpread(CENT),
    slippage_model=NoSlippage(),
    impact_model=SquareRootImpact(Decimal("0.01")),
    commission_model=FixedCommission(Decimal("1.00")),
    fee_model=NoFee(),
    tax_model=NoTax(),
)
START_CASH = Decimal("1000000")
SEED = 74
CONTEXTS = context_factory(FixedClock(0.0), DiscardingLogger())


def run_config() -> RunConfig:
    huge = Decimal("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX74", "USD", "Example 74", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=False, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(Decimal("10000"), huge),
                position=PositionLimit(huge, huge),
                exposure=ExposureLimit(huge, huge),
                leverage=LeverageLimit(Decimal("2")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(huge, "America/New_York"),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            simulator=ExecutionSimulator(cost_model=COSTS),
            instruments=REGISTRY,
            # A child is never filled at the bar it was released at.
            fill_timing=FillTiming.NEXT_EVENT,
        ),
        # No fill -- whole or child -- takes more than a quarter of its bar.
        fill_policy=LiquidityCappedFill(participation_rate=Decimal("0.25")),
        seed=SEED,
        start_timestamp=1.0,
    )


def run(name: str) -> BacktestResult:
    return BacktestEngine.run(
        run_config(), dataset(), running(), CONTEXTS, execution=plan(CHOICES[name])
    )


# --------------------------------------------------------------------------- #
# 4. Measuring each run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Measured:
    name: str
    filled: Decimal
    fills: int
    status: OrderStatus
    average: Decimal | None
    commission: Decimal
    shortfall_bps: Decimal | None


def benchmarks() -> ExecutionBenchmarks:
    window = range(1, 31)
    traded = sum((volume(m) for m in window), Decimal("0"))
    vwap = sum((price(m) * volume(m) for m in window), Decimal("0")) / traded
    return ExecutionBenchmarks(
        decision_price=price(0),
        arrival_price=price(0),
        interval_vwap=vwap,
        end_price=price(30),
        source="example 74's synthetic bars: the open's close, the window's VWAP and close",
    )


def measure(name: str, result: BacktestResult) -> Measured:
    (parent,) = result.state.oms.orders.orders()
    order_id = str(parent.order_id.value)
    reports = tuple(r for r in result.state.execution.history if r.order_id == order_id)
    request = OrderRequest(
        order_id=order_id,
        strategy_id=STRATEGY_ID,
        asset_id=parent.asset_id,
        side=parent.side,
        quantity=parent.quantity,
        price=Decimal(parent.metadata["reference_price"]),
        timestamp=parent.created_at,
        contributions=(StrategyContribution(STRATEGY_ID, parent.quantity),),
    )
    shortfall = implementation_shortfall(
        OrderExecution(
            order=request,
            fills=reports,
            currency="USD",
            outcome=parent.status,
            benchmarks=benchmarks(),
            limit_price=None,
            timeline=None,
            fill_midpoints=None,
            account_id="ACC-EX74",
        )
    )
    return Measured(
        name,
        parent.filled_quantity,
        len(reports),
        parent.status,
        shortfall.average_fill_price,
        sum((r.commission for r in reports), Decimal("0")),
        shortfall.total_bps,
    )


# --------------------------------------------------------------------------- #
# 5. The replay, paused with a child working, resumed from its payload
# --------------------------------------------------------------------------- #


def replayed(name: str, pause: int | None = None) -> BacktestResult:
    execution = plan(CHOICES[name])
    if pause is None:
        return ReplayBacktest.run(
            run_config(), dataset(), running(), CONTEXTS, execution=execution
        ).backtest
    paused = ReplayBacktest.run(
        run_config(),
        dataset(),
        running(),
        CONTEXTS,
        pause_when=lambda progress: progress.records_replayed == pause,
        execution=execution,
    )
    assert isinstance(paused, PausedReplay)
    config = run_config()
    objects = RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY_ID: Buyer()},
            instruments=REGISTRY,
        ),
        fill_policy=config.fill_policy,
    )
    payload = serialize(capture(paused))
    restored = restore(
        from_primitives(deserialize(payload)), objects, dataset(), RunSchedule(), execution
    )
    return ReplayBacktest.resume(restored, CONTEXTS).backtest


def number(value: Decimal | None, places: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN):,}"


def main() -> None:
    print("Example 74: one decision, five ways to execute it, measured on the same bars")
    print(f"  buy {QUANTITY:,} {STOCK.symbol} at the open; window 30 minutes; fills capped at 25%")
    print()
    print(
        f"  {'execution':<14}{'filled':>8}{'fills':>7}{'status':>11}{'avg px':>9}"
        f"{'comm':>8}{'shortfall':>12}"
    )
    results = {name: run(name) for name in CHOICES}
    for name, result in results.items():
        m = measure(name, result)
        print(
            f"  {m.name:<14}{number(m.filled, 0):>8}{m.fills:>7}{m.status.value:>11}"
            f"{number(m.average):>9}{number(m.commission):>8}{number(m.shortfall_bps, 1):>9} bps"
        )
    print()

    twap = results["twap"]
    assert isinstance(twap, AlgorithmicResult)
    book = twap.algorithms
    (parent,) = twap.state.oms.orders.orders()
    worked = book.algorithm_for(str(parent.order_id.value))
    assert worked is not None
    print("  TWAP's children, each settled on the one parent order:")
    for progress in worked.children.values():
        child = progress.child
        print(
            f"    #{child.sequence}  released {int((child.released_at - OPEN) / 60):>2}m  "
            f"{number(child.quantity, 0):>5} asked  {number(progress.filled, 0):>5} filled  "
            f"{'working' if progress.outcome is None else progress.outcome.value}"
        )
    held = str(parent.order_id.value) in twap.state.allocation.reservations
    print(f"  the parent's capital reservation, released when it filled: held={held}")
    print()

    # A replay records its own mode, so it is compared by what it did.
    replay = replayed("twap")
    assert isinstance(replay, AlgorithmicResult)
    assert capture_book(replay.algorithms) == capture_book(book)
    assert replay.fills == twap.fills and replay.valuation == twap.valuation
    resumed = replayed("twap", pause=7)
    assert digest_run(resumed).result_id == digest_run(replay).result_id
    rerun = run("twap")
    assert digest_run(rerun).result_id == digest_run(twap).result_id
    print("  replay: the same children, fills and valuation as the backtest")
    print("  replay paused at bar 7 with a child working, resumed from JSON: the same replay")
    print(f"  rerun: the same result, {digest_run(twap).result_id[:16]}...")


if __name__ == "__main__":
    main()
