"""Shared setup for the v3.9 execution-contract examples (61-65).

Every example reads the same small world, written out below:

* **one instrument**, ACME, listed on XNAS in US dollars, identified by the
  ``asset_id`` its instrument record derives -- the identity every AlphaLab path
  uses;
* **three execution venues** reached through three adapters an application would
  own, labelled ``adapter-north``, ``adapter-south`` and ``adapter-east``. Each
  declares what it can do as a
  :class:`~alphalab.core.capabilities.CapabilityDeclaration` and carries a cost
  model of the v3.3 kind. They are labels: AlphaLab names no vendor, holds no
  credential and opens no connection;
* **a scripted venue** at the external boundary -- :class:`ScriptedVenue` --
  standing in for whatever an application's adapter speaks. It records a
  submission and does nothing else by itself; everything a venue says
  afterwards is handed to AlphaLab as a normalized
  :class:`~alphalab.broker.lifecycle.VenueEvent`, exactly as an adapter would
  after translating its vendor's messages;
* **a morning in eight half-hour intervals**, 09:30 to 13:30 New York time on
  3 June 2024, with the volume a desk's profile expects in each, the volume the
  tape then printed and the midpoint at each interval's start -- all written out
  as numbers, so every figure an example derives is the same on every machine;
* **a desk of two strategies**, MOMENTUM and MEANREV, that trade ACME through
  the canonical execution path with routing left ``EXTERNAL``: an accepted order
  stays working in the OMS, holding its capital, until a venue reports on it.

Nothing here fetches anything, reads a clock or draws a random number.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.broker import (
    ANY_LISTING_VENUE,
    AccountCapability,
    BrokerEngine,
    BrokerEvent,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerState,
    Capability,
    CapabilityDeclaration,
    MarketCapability,
    OrderSubmitted,
    PaperBroker,
    Support,
    validate_order_submission,
)
from alphalab.common.ids import new_id
from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.execution import (
    ExecutionCostModel,
    FixedCommission,
    IntervalVolume,
    NoImpact,
    NoSlippage,
    NoTax,
    ProportionalFee,
    QuotedHalfSpread,
    VenueProfile,
    VenueQuote,
    VolumeProfile,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.market.quote import Quote
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
from alphalab.runtime import (
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.supervisor import RuntimeSupervisor

YES, NO, SILENT = Support.SUPPORTED, Support.UNSUPPORTED, Support.UNDECLARED

# --------------------------------------------------------------------------- #
# The instrument and the clock
# --------------------------------------------------------------------------- #

#: The instrument every example trades.
ACME = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD")
ASSET_ID = ACME.asset_id
LISTING = "XNAS"
ACCOUNT_ID = "DESK-ACCOUNT-1"

#: 09:30 New York on 3 June 2024, as Unix seconds, and the half-hour step.
OPEN = 1_717_421_400.0
HALF_HOUR = 1_800.0
INTERVALS = 8
CLOSE = OPEN + INTERVALS * HALF_HOUR


def interval(index: int) -> tuple[float, float]:
    """The ``index``-th half hour, as ``[start, end)``."""

    return OPEN + index * HALF_HOUR, OPEN + (index + 1) * HALF_HOUR


def clock(instant: float) -> str:
    """A Unix instant on this morning as New York wall-clock time, for printing."""

    seconds = instant - OPEN + 9.5 * 3600
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    whole = f"{int(hours):02d}:{int(minutes):02d}"
    return whole if secs == 0 else f"{whole}:{secs:06.3f}"


# --------------------------------------------------------------------------- #
# Three venues, declared
# --------------------------------------------------------------------------- #


def declaration(
    adapter: str,
    *,
    limit_orders: bool,
    shorting: Support,
    streaming: bool,
    account_margin: Support = NO,
) -> CapabilityDeclaration:
    """One adapter's declaration: equities on every listing venue, options declared absent.

    The desk's account is permitted futures, but no adapter declares a futures
    market, so a futures order is *undeclared* at every venue -- neither
    refused nor permitted.
    """

    order_types = {OrderType.MARKET, OrderType.STOP}
    if limit_orders:
        order_types.add(OrderType.LIMIT)
    return CapabilityDeclaration(
        adapter_id=adapter,
        supported_features=frozenset(
            {Capability.CANCEL_REPLACE, *((Capability.STREAMING,) if streaming else ())}
        ),
        unsupported_features=frozenset(() if streaming else (Capability.STREAMING,)),
        markets=(
            MarketCapability(
                asset_class=AssetType.EQUITY,
                listing_venue=ANY_LISTING_VENUE,
                order_types=frozenset(order_types),
                time_in_force=frozenset({TimeInForce.DAY, TimeInForce.IOC}),
                short_selling=shorting,
                fractional_quantities=YES,
                extended_hours=NO,
                bracket_orders=NO,
            ),
        ),
        unsupported_asset_classes=frozenset({AssetType.OPTION}),
        accounts=(
            AccountCapability(
                account_id=ACCOUNT_ID,
                asset_classes=frozenset({AssetType.EQUITY, AssetType.FUTURE}),
                margin=account_margin,
                short_selling=YES,
            ),
        ),
    )


#: Takes limit orders, permits short sales, streams its reports.
NORTH = declaration("adapter-north", limit_orders=True, shorting=YES, streaming=True)
#: Takes limit orders; its adapter says nothing about short sales, and it polls.
SOUTH = declaration("adapter-south", limit_orders=True, shorting=SILENT, streaming=False)
#: Cheapest, but takes no limit orders.
EAST = declaration("adapter-east", limit_orders=False, shorting=YES, streaming=True)

DECLARATIONS = {"VENUE-NORTH": NORTH, "VENUE-SOUTH": SOUTH, "VENUE-EAST": EAST}


def costs(fee: str) -> ExecutionCostModel:
    """Half the quoted spread paid, and a proportional venue fee: the v3.3 roles, named."""

    return ExecutionCostModel(
        spread_model=QuotedHalfSpread(),
        slippage_model=NoSlippage(),
        impact_model=NoImpact(),
        commission_model=FixedCommission(Decimal("0")),
        fee_model=ProportionalFee(Decimal(fee)),
        tax_model=NoTax(),
    )


#: What routing knows about each venue: its declaration, its cost model and its
#: measured round-trip latency in seconds.
PROFILES = (
    VenueProfile("VENUE-NORTH", NORTH, costs("0.00010"), Decimal("0.004")),
    VenueProfile("VENUE-SOUTH", SOUTH, costs("0.00005"), Decimal("0.002")),
    VenueProfile("VENUE-EAST", EAST, costs("0"), Decimal("0.001")),
)

#: The source every example quote names.
TAPE = "example tape: consolidated quotes as written in _execution_world.py"


def venue_quote(venue: str, bid: str, ask: str, size: str, at: float) -> VenueQuote:
    return VenueQuote(
        venue, ASSET_ID, Decimal(bid), Decimal(ask), Decimal(size), Decimal(size), "USD", at, TAPE
    )


def quotes(at: float) -> tuple[VenueQuote, ...]:
    """What each venue shows for ACME at ``at``."""

    return (
        venue_quote("VENUE-NORTH", "99.98", "100.02", "1200", at),
        venue_quote("VENUE-SOUTH", "99.99", "100.01", "300", at),
        venue_quote("VENUE-EAST", "99.99", "100.01", "2000", at),
    )


# --------------------------------------------------------------------------- #
# The morning's volume and prices
# --------------------------------------------------------------------------- #

#: The volume a desk's profile expects in each half hour: heavy at the open,
#: light towards lunch. Twenty-day average, in shares.
EXPECTED_VOLUME = ("180000", "110000", "80000", "60000", "55000", "70000", "95000", "170000")

#: What the tape then printed in each half hour. The fifth interval's print was
#: lost by the feed, so it is ``None`` -- not zero.
OBSERVED_VOLUME: tuple[str | None, ...] = (
    "160000",
    "120000",
    "70000",
    "65000",
    None,
    "75000",
    "90000",
    "185000",
)

#: The ACME midpoint at the start of each half hour, and at 13:30.
MIDPOINTS = ("100.00", "100.04", "100.08", "100.06", "100.10", "100.14", "100.18", "100.22")
END_MIDPOINT = Decimal("100.25")


def volume_profile() -> VolumeProfile:
    return VolumeProfile(
        tuple(IntervalVolume(*interval(i), Decimal(v)) for i, v in enumerate(EXPECTED_VOLUME)),
        "desk profile: twenty-day average volume per half hour",
    )


def observed(index: int) -> IntervalVolume:
    """The volume the tape printed over the ``index``-th half hour."""

    value = OBSERVED_VOLUME[index]
    return IntervalVolume(*interval(index), None if value is None else Decimal(value))


def midpoint(index: int) -> Decimal:
    return Decimal(MIDPOINTS[index])


# --------------------------------------------------------------------------- #
# The external boundary
# --------------------------------------------------------------------------- #


class ScriptedVenue(PaperBroker):
    """A venue that takes a submission and then waits to be told what happened.

    The reference adapter acknowledges and fills on its own; a real venue does
    neither synchronously. This one records a submission as ``SUBMITTED`` and
    nothing more: every acknowledgement, fill, rejection and cancel arrives
    afterwards as a normalized event.
    """

    def submit_order(
        self, state: BrokerState, order: BrokerOrder, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        validate_order_submission(state, order)
        submitted = replace(order, status=BrokerOrderStatus.SUBMITTED, updated_at=timestamp)
        event = OrderSubmitted(str(new_id()), timestamp, order.broker_order_id, order.oms_order_id)
        return (
            replace(
                state,
                orders=state.orders.set(order.broker_order_id, submitted),
                events=state.events.append(event),
            ),
            (event,),
        )


def connected_venue(name: str, cash: Decimal) -> BrokerState:
    """A fresh mirror of one venue account, connected a minute before the open."""

    state = BrokerEngine.initialize(name, cash, "USD")
    state, _ = ScriptedVenue().connect(state, OPEN - 60.0)
    return state


# --------------------------------------------------------------------------- #
# The desk: two strategies on the canonical execution path
# --------------------------------------------------------------------------- #

DESK_CASH = Decimal("5000000")
STRATEGIES = ("MOMENTUM", "MEANREV")


class PlannedStrategy(BaseStrategy):
    """Asks for a stated quantity of ACME at stated instants, and nothing else."""

    def __init__(self, strategy_id: str, plan: Mapping[float, Decimal]) -> None:
        self._strategy_id = strategy_id
        self._plan = dict(plan)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        delta = self._plan.get(event.quote.timestamp)
        if delta is None:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=ASSET_ID,
                target=delta,
                timestamp=event.quote.timestamp,
            ),
        )


class _Clock:
    """The strategy context's clock. The desk's strategies never read it."""

    def now(self) -> float:
        return OPEN


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def context_factory(strategy_id: str) -> StrategyContext:
    return StrategyContext(
        portfolio=NoPortfolio(),
        market=NoMarket(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=NoRiskView(),
        config={"strategy_id": strategy_id},
        orders=NoOrders(),
    )


def desk(plans: Mapping[str, Mapping[float, Decimal]]) -> ExecutionPipelineState:
    """The desk's execution path, with each strategy running its plan.

    Routing is ``EXTERNAL``: an order the OMS accepts is left working, with its
    capital reserved, until a venue reports on it through the broker boundary.
    """

    runtime = create_runtime()
    for strategy_id, plan in plans.items():
        runtime = register_strategy(runtime, strategy_id, PlannedStrategy(strategy_id, plan))
    running = {}
    for strategy_id, strategy_state in runtime.strategies.items():
        step, _ = RuntimeSupervisor.configure(strategy_state, {}, OPEN - 600.0)
        step, _ = RuntimeSupervisor.initialize(step, OPEN - 599.0)
        step, _ = RuntimeSupervisor.subscribe(step, frozenset({"quotes"}), OPEN - 598.0)
        step, _ = RuntimeSupervisor.start(step, OPEN - 597.0)
        running[strategy_id] = step
    huge = Decimal("100000000")
    config = ExecutionPipelineConfig(
        account=Account(ACCOUNT_ID, "USD", "Execution desk", OPEN - 600.0),
        starting_cash=DESK_CASH,
        budget=CapitalBudget(
            global_capital=DESK_CASH,
            maximum_exposure=DESK_CASH * 2,
            cash_buffer=Decimal("0"),
            strategy_budgets=dict.fromkeys(plans, DESK_CASH / 2),
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=False, enforce_integer_quantities=True
        ),
        risk_limits=RiskLimits(
            order_size=OrderSizeLimit(Decimal("50000"), huge),
            position=PositionLimit(huge, huge),
            exposure=ExposureLimit(huge, huge),
            leverage=LeverageLimit(Decimal("10")),
            margin=MarginLimit(Decimal("1.00")),
            daily_loss=DailyLossLimit(huge, "UTC"),
            drawdown=DrawdownLimit(Decimal("1.00")),
        ),
        routing=ExecutionRouting.EXTERNAL,
    )
    return ExecutionPipeline.initialize(config, replace(runtime, strategies=running), OPEN - 596.0)


def desk_quote(at: float, mid: Decimal) -> Quote:
    """The consolidated ACME quote the desk's strategies see: a one-cent spread."""

    half = Decimal("0.005")
    return Quote(
        asset_id=ASSET_ID,
        timestamp=at,
        bid=mid - half,
        ask=mid + half,
        bid_size=Decimal("50000"),
        ask_size=Decimal("50000"),
        venue="CONSOLIDATED",
        currency="USD",
    )


# --------------------------------------------------------------------------- #
# Printing
# --------------------------------------------------------------------------- #


def banner(number: int, title: str) -> None:
    print("=" * 72)
    print(f"AlphaLab Example {number} : {title}")
    print("=" * 72)


def section(title: str) -> None:
    print()
    print(f"-- {title} " + "-" * max(0, 66 - len(title)))


def say(text: str, indent: str = "  ") -> None:
    """A paragraph of commentary, wrapped."""

    print(
        textwrap.fill(
            text,
            width=76,
            initial_indent=indent,
            subsequent_indent=indent,
            break_long_words=False,
            break_on_hyphens=False,
        )
    )


def refusal(what: str, error: Exception) -> None:
    """Print a refusal: what was asked, the exception, and its message, wrapped."""

    print(f"  {what} -> {type(error).__name__}:")
    say(str(error), indent="    ")


def number(value: Decimal | None, places: int | None = None) -> str:
    """A decimal for printing: grouped, trailing zeros dropped, ``n/a`` for ``None``."""

    if value is None:
        return "n/a"
    if places is not None:
        return f"{value:,.{places}f}"
    if value == value.to_integral_value():
        return f"{value:,.0f}"
    return f"{value.normalize():,f}"


def short(identity: str) -> str:
    """The first twelve characters of a digest: enough to tell two apart in print."""

    return identity[:12]
