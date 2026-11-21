"""
AlphaLab Examples
=================

Example 82 : Research Execution Across Venues

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 74 (evaluate an execution algorithm)

Topics
------

• One decision -- buy 3,000 shares -- worked by a TWAP of limit children that
  wait their turn in a queue model, at venue ALPHA, at venue BETA, and routed
  between them by each venue's own costs
• What each run paid, attributed to the venue that charged it, and each run's
  implementation shortfall
• The same order kept from the market by each venue's latency, filling at a
  later bar's price than a stamp would say
• A two-venue run whose costs reconcile venue by venue (GR-4)

What this shows
---------------

Plan scenario S-09 (v4.3, ADR-0060, ADR-0061). Where a strategy trades is part
of what a backtest measures. Each venue is declared with its own execution
model -- its spread, its commission, its latency -- and every order is routed to
one: by the venue its terms name, by the run's router, or by its listing venue.
The routed venue's model fills it, and the execution report says which venue
did, so a run's costs reconcile per venue. The bars are **synthetic** and the
venues' costs are stated, not measured: the comparison is between execution
choices, not a claim about any real venue.
``tests/integration/test_execution_research_end_to_end.py`` holds it.

Run

    python examples/82_research_execution_across_venues.py
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import (
    AlgorithmAssignment,
    BacktestEngine,
    BacktestResult,
    ExecutionMode,
    ExecutionPlan,
    MarketDataset,
    PriceTimeQueue,
    RunConfig,
)
from alphalab.common.order_terms import MARKET, OrderTerms, TimeInForce
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import AssetType
from alphalab.core.order_request import OrderRequest
from alphalab.execution import CheapestVenue, VenueExecution
from alphalab.execution.algorithms import TWAP, Urgency
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.costs import (
    ExecutionCostModel,
    FixedHalfSpread,
    NoFee,
    NoImpact,
    NoSlippage,
    NoTax,
)
from alphalab.execution.latency import ConstantLatency
from alphalab.execution.policy import FillTiming, LatencyPricing, LiquidityCappedFill
from alphalab.execution.quality import (
    ExecutionBenchmarks,
    OrderExecution,
    implementation_shortfall,
)
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.market.bar import Bar, TimeFrame
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
from alphalab.strategy import (
    BaseStrategy,
    DiscardingLogger,
    FixedClock,
    Intent,
    RuntimeState,
    RuntimeSupervisor,
    StrategyContext,
    context_factory,
    create_runtime,
    register_strategy,
)

# --------------------------------------------------------------------------- #
# 1. The market: one hour of minute bars
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
                Decimal("4000"),
                None,
                None,
                TimeFrame.M1,
            )
        )
    return MarketDataset.of("EX82-MINUTES", bars)


# --------------------------------------------------------------------------- #
# 2. The venues, each with its own model
# --------------------------------------------------------------------------- #


def venue(half_spread: str, per_share: str, latency: float) -> VenueExecution:
    """One venue's stated costs: a half-spread, a commission per share, a latency."""

    return VenueExecution(
        ExecutionSimulator(
            cost_model=ExecutionCostModel(
                spread_model=FixedHalfSpread(Decimal(half_spread)),
                slippage_model=NoSlippage(),
                impact_model=NoImpact(),
                commission_model=PerShareCommission(Decimal(per_share)),
                fee_model=NoFee(),
                tax_model=NoTax(),
            ),
            latency_model=ConstantLatency(latency),
        )
    )


#: ALPHA: a cent of half-spread, a fifth of a cent a share, half a second.
#: BETA: half a cent of half-spread, half a cent a share, ninety seconds.
ALPHA = venue("0.01", "0.002", 0.5)
BETA = venue("0.005", "0.005", 90.0)
VENUES: Mapping[str, Mapping[str, VenueExecution]] = {
    "alpha": {"ALPHA": ALPHA},
    "beta": {"BETA": BETA},
    "routed": {"ALPHA": ALPHA, "BETA": BETA},
}


# --------------------------------------------------------------------------- #
# 3. The decision, and the run
# --------------------------------------------------------------------------- #

STRATEGY_ID = "EX82-BUYER"
QUANTITY = Decimal("3000")
LIMIT = Decimal("50.10")
START_CASH = Decimal("1000000")
SEED = 82
CONTEXTS = context_factory(FixedClock(0.0), DiscardingLogger())


class Buyer(BaseStrategy):
    """Buys at the open what ``orders`` states: each a quantity and its terms."""

    def __init__(self, orders: Iterable[tuple[Decimal, OrderTerms]]) -> None:
        self._orders = tuple(orders)

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.bar.timestamp != OPEN:
            return ()
        return tuple(
            Intent(STRATEGY_ID, STOCK.asset_id, quantity, timestamp=OPEN, terms=terms)
            for quantity, terms in self._orders
        )


def running(orders: Iterable[tuple[Decimal, OrderTerms]]) -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, Buyer(orders))
    strategy = state.strategies[STRATEGY_ID]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(strategy, frozenset({"bars"}), 1.2)
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return replace(state, strategies={STRATEGY_ID: strategy})


def run_config(
    venues: Mapping[str, VenueExecution],
    pricing: LatencyPricing = LatencyPricing.STAMP,
) -> RunConfig:
    huge = Decimal("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX82", "USD", "Example 82", 1.0),
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
            instruments=REGISTRY,
            fill_timing=FillTiming.NEXT_EVENT,
            latency_pricing=pricing,
            venues=venues,
            # The cheapest of the declared venues, by each one's own cost model.
            router=CheapestVenue(),
        ),
        fill_policy=LiquidityCappedFill(participation_rate=Decimal("0.25")),
        seed=SEED,
        start_timestamp=1.0,
    )


#: The parent: 3,000 shares at a limit of 50.10, good until cancelled, worked
#: by six TWAP slices over half an hour. Its children are limit orders at the
#: same limit, and they wait their turn in the queue.
PARENT = OrderTerms.limit(LIMIT, TimeInForce.GTC)
PLAN = ExecutionPlan(AlgorithmAssignment(TWAP(6, Urgency.neutral()), 30 * MINUTE, Decimal("1")))


def queued(name: str) -> BacktestResult:
    """Part 1: the parent, worked in queued children at ``name``'s venues."""

    return BacktestEngine.run(
        run_config(VENUES[name]),
        dataset(),
        running(((QUANTITY, PARENT),)),
        CONTEXTS,
        execution=PLAN,
        queue=PriceTimeQueue(),
    )


def latency_priced(pricing: LatencyPricing) -> BacktestResult:
    """Part 2: a market order of 1,000 at BETA, whose latency is ninety seconds."""

    return BacktestEngine.run(
        run_config(VENUES["beta"], pricing),
        dataset(),
        running(((Decimal("1000"), MARKET),)),
        CONTEXTS,
    )


def two_venues() -> BacktestResult:
    """Part 3 (GR-4): 1,000 shares to each venue, by name, in one run."""

    return BacktestEngine.run(
        run_config(VENUES["routed"]),
        dataset(),
        running(
            (
                (Decimal("1000"), OrderTerms(venue="ALPHA")),
                (Decimal("1000"), OrderTerms(venue="BETA")),
            )
        ),
        CONTEXTS,
    )


# --------------------------------------------------------------------------- #
# 4. Measuring each run, venue by venue
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class VenueCosts:
    """What one venue filled and charged: its spread is in its fill prices."""

    venue: str
    filled: Decimal
    fills: int
    commission: Decimal
    notional: Decimal

    @property
    def average_price(self) -> Decimal:
        return (self.notional / self.filled).quantize(Decimal("0.0001"))


def costs_by_venue(result: BacktestResult) -> dict[str, VenueCosts]:
    """Each venue's fills and what it charged: the reports say which venue filled each."""

    found: dict[str, VenueCosts] = {}
    for report in result.state.execution.history:
        held = found.get(report.venue)
        if held is None:
            held = VenueCosts(report.venue, Decimal("0"), 0, Decimal("0"), Decimal("0"))
        found[report.venue] = VenueCosts(
            report.venue,
            held.filled + report.fill_quantity,
            held.fills + 1,
            held.commission + report.commission,
            held.notional + report.fill_quantity * report.fill_price,
        )
    return found


def benchmarks() -> ExecutionBenchmarks:
    return ExecutionBenchmarks(
        decision_price=price(0),
        arrival_price=price(0),
        interval_vwap=None,
        end_price=price(30),
        source="example 82's synthetic bars: the open's close and the half hour's close",
    )


def shortfall_bps(result: BacktestResult) -> Decimal | None:
    """The parent's implementation shortfall, in basis points of the decision."""

    (parent,) = result.state.oms.orders.orders()
    order_id = str(parent.order_id.value)
    reports = tuple(r for r in result.state.execution.history if r.order_id == order_id)
    request = OrderRequest(
        order_id=order_id,
        strategy_id=STRATEGY_ID,
        asset_id=parent.asset_id,
        side=parent.side,
        quantity=parent.quantity,
        price=price(0),
        timestamp=parent.created_at,
        contributions=(StrategyContribution(STRATEGY_ID, parent.quantity),),
    )
    measured = implementation_shortfall(
        OrderExecution(
            order=request,
            fills=reports,
            currency="USD",
            outcome=parent.status,
            benchmarks=benchmarks(),
            limit_price=LIMIT,
            timeline=None,
            fill_midpoints=None,
            account_id="ACC-EX82",
        )
    )
    return measured.total_bps


def routed_to(result: BacktestResult) -> list[tuple[str, str]]:
    """Each order's routing record: the venue, and how it was chosen."""

    return [
        (order.metadata["venue"], order.metadata["venue_by"])
        for order in result.state.oms.orders.orders()
    ]


def number(value: Decimal | None, places: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{places}f}"


def main() -> None:
    print("Example 82 -- research execution across venues (plan scenario S-09)\n")

    print("1. Three thousand shares at 50.10, six queued TWAP slices")
    for name in ("alpha", "beta", "routed"):
        result = queued(name)
        ((venue_code, how),) = routed_to(result)
        costs = costs_by_venue(result).get(venue_code)
        filled = "nothing" if costs is None else f"{number(costs.filled, 0)} in {costs.fills} fills"
        charged = "nothing" if costs is None else number(costs.commission)
        print(
            f"   {name:>6}: routed to {venue_code} by {how}; filled {filled}; commission "
            f"{charged}; shortfall {number(shortfall_bps(result))} bps"
        )

    print("\n2. A thousand shares at the market, ninety seconds from BETA")
    for pricing in LatencyPricing:
        (report,) = latency_priced(pricing).state.execution.history
        print(
            f"   {pricing.value:>18}: filled at {number(report.fill_price, 4)}, stamped "
            f"{report.timestamp - OPEN:.0f} s after the open"
        )
    print(
        "   a stamp only moves the time: the next bar's close plus BETA's half-spread; kept"
        "\n   from the market, the order meets the bar its ninety seconds end within"
    )

    print("\n3. A thousand shares to each venue in one run (GR-4)")
    result = two_venues()
    venues = costs_by_venue(result)
    for code in sorted(venues):
        costs = venues[code]
        print(
            f"   {code:>5}: {number(costs.filled, 0)} shares at an average "
            f"{number(costs.average_price, 4)}, commission {number(costs.commission)}"
        )
    total = sum((f.commission for f in result.fills), Decimal("0"))
    print(f"   total commission {number(total)}: the venues' sum")


if __name__ == "__main__":
    main()
