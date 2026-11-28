"""Example 84's carry book, defined where every name and number is stated (ADR-0067, plan S-05).

A dollar account holds a million Australian and a million New Zealand dollars
bought one month forward. It rolls each forward as a swap on the day the
pair's spot value date reaches the forward's own: the near leg sells the
currency at spot for that day, and the far leg buys it a month on. It runs for
the weekdays of 2025. Everything it turns on is stated here:

* the calendars: the venue's, and each currency's holidays;
* the pairs and their value-date rules: a US-dollar pair counted in the other
  currency's calendar and settled where both are open;
* the prices, and the interest rates that set each forward;
* the strategy, capital, limits, costs and fills, and the seed.

The prices are **synthetic**. Spot rates are random walks drawn with
``random()`` and arithmetic alone, so every host draws the same bits. Each
forward is its spot carried a month at the stated rates, so the Australian and
New Zealand dollars trade at a discount, which is the carry the book earns
while spot stands still. The holidays are the example's declaration of each
market's calendar for 2025 and early 2026, not a reference: a run that relies
on a calendar takes it from a pack or states its source.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    ExecutionMode,
    MarketDataset,
    RunConfig,
    RunSchedule,
)
from alphalab.common.order_terms import MARKET
from alphalab.conventions.business_days import (
    BusinessDayConvention,
    Tenor,
    TenorUnit,
    ValueDateRule,
    forward_value_date,
    spot_value_date,
)
from alphalab.core.enums import AssetType
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.execution.commission import FixedCommission
from alphalab.execution.policy import FillTiming, ImmediateFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.instrument.terms import FxKind, FxTerms, FxValueDateRule
from alphalab.market.quote import Quote
from alphalab.portfolio.account import Account
from alphalab.portfolio.fx import FxRate, FxRates
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
from alphalab.runtime.calendars import VenueCalendars
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.strategy.context import (
    NoMarket,
    NoOrders,
    NoPortfolio,
    NoRiskView,
    StrategyContext,
)
from alphalab.strategy.events import Intent, IntentKind, Leg
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

STRATEGY_ID = "fx-carry"
START_CASH = Decimal("2000000")
#: Units of each currency held forward.
NOTIONAL = Decimal("1000000")
#: Each fill's commission, in dollars.
COMMISSION = Decimal("10.00")
#: The annual interest rates that set each forward, simple: stated, synthetic.
RATES = {"USD": Decimal("0.0300"), "AUD": Decimal("0.0450"), "NZD": Decimal("0.0500")}
ONE_MONTH = Tenor(1, TenorUnit.MONTHS)
#: The forwards' convention: modified following, under the end-of-month rule.
CONVENTION = BusinessDayConvention.MODIFIED_FOLLOWING


# --------------------------------------------------------------------------- #
# Calendars: the venue's, and each currency's
# --------------------------------------------------------------------------- #


def _weekdays(calendar_id: str, zone: str, holidays: Iterable[date]) -> MarketCalendar:
    sessions = dict.fromkeys(range(5), (SessionWindow(time(0, 0), time(23, 0)),))
    return MarketCalendar(calendar_id, zone, sessions, frozenset(holidays))


#: The venue the pairs are dealt on: open every weekday, in UTC.
VENUE = _weekdays("XFXV", "UTC", ())
#: New York's banks', Sydney's and Wellington's holidays, as this example
#: declares them: 2025's, and 2026's to mid-February, where the last forwards
#: settle.
US_HOLIDAYS = (
    "2025-01-01 2025-01-20 2025-02-17 2025-05-26 2025-06-19 2025-07-04 2025-09-01 "
    "2025-10-13 2025-11-11 2025-11-27 2025-12-25 2026-01-01 2026-01-19 2026-02-16"
)
AU_HOLIDAYS = (
    "2025-01-01 2025-01-27 2025-04-18 2025-04-21 2025-04-25 2025-06-09 2025-08-04 "
    "2025-10-06 2025-12-25 2025-12-26 2026-01-01 2026-01-26"
)
NZ_HOLIDAYS = (
    "2025-01-01 2025-01-02 2025-01-20 2025-02-06 2025-04-18 2025-04-21 2025-04-25 "
    "2025-06-02 2025-06-20 2025-10-27 2025-12-25 2025-12-26 2026-01-01 2026-01-02 "
    "2026-01-19 2026-02-06"
)


def _holidays(days: str) -> tuple[date, ...]:
    return tuple(date.fromisoformat(day) for day in days.split())


USNY = _weekdays("USNY", "America/New_York", _holidays(US_HOLIDAYS))
AUSY = _weekdays("AUSY", "Australia/Sydney", _holidays(AU_HOLIDAYS))
NZWE = _weekdays("NZWE", "Pacific/Auckland", _holidays(NZ_HOLIDAYS))
CALENDARS = VenueCalendars({"XFXV": VENUE, "USNY": USNY, "AUSY": AUSY, "NZWE": NZWE})
_BY_ID = {calendar.calendar_id: calendar for calendar in (USNY, AUSY, NZWE)}


# --------------------------------------------------------------------------- #
# The pairs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Pair:
    """One currency against the dollar: its spot record, its one-month forward, its rule."""

    base: str
    start: Decimal
    spot: InstrumentRecord
    forward: InstrumentRecord

    @property
    def rule(self) -> ValueDateRule:
        """The pair's value-date rule, its calendars resolved: what the strategy counts by."""

        terms = self.spot.terms
        assert isinstance(terms, FxTerms)
        dates = terms.value_dates
        return ValueDateRule(
            dates.spot_lag,
            tuple(_BY_ID[i] for i in dates.counted),
            tuple(_BY_ID[i] for i in dates.settles),
        )


def _pair(base: str, calendar: str, start: str) -> Pair:
    # Counted in the other currency's calendar alone; settled where both are open.
    spot = FxValueDateRule(2, (calendar,), (calendar, "USNY"))
    month = replace(spot, tenor=ONE_MONTH, convention=CONVENTION, end_of_month=True)
    return Pair(
        base,
        Decimal(start),
        InstrumentRecord(
            f"{base}USD",
            AssetType.FOREX,
            "XFXV",
            "USD",
            terms=FxTerms(base, "USD", FxKind.SPOT, spot),
        ),
        InstrumentRecord(
            f"{base}USD1M",
            AssetType.FOREX,
            "XFXV",
            "USD",
            terms=FxTerms(base, "USD", FxKind.FORWARD, month),
        ),
    )


PAIRS = (_pair("AUD", "AUSY", "0.65000"), _pair("NZD", "NZWE", "0.60000"))
BY_FORWARD = {pair.forward.asset_id: pair for pair in PAIRS}
INSTRUMENTS: InstrumentRegistry = register_instruments(
    InstrumentRegistry(), [record for pair in PAIRS for record in (pair.spot, pair.forward)]
)


# --------------------------------------------------------------------------- #
# Prices: synthetic
# --------------------------------------------------------------------------- #


def _instants() -> tuple[float, ...]:
    day, out = date(2025, 1, 2), []
    while day.year == 2025:
        if day.weekday() < 5:
            out.append(datetime.combine(day, time(21, 0), UTC).timestamp())
        day += timedelta(days=1)
    return tuple(out)


#: Every weekday of 2025 from 2 January, at 21:00 UTC.
INSTANTS = _instants()
_FIVE = Decimal("0.00001")


def _spots() -> dict[str, tuple[Decimal, ...]]:
    """Each pair's spot at every instant: a random walk of at most 0.6% a day."""

    draw = random.Random(84)
    paths: dict[str, tuple[Decimal, ...]] = {}
    for pair in PAIRS:
        level, path = float(pair.start), []
        for _ in INSTANTS:
            path.append(Decimal(f"{level:.5f}"))
            level *= 1.0 + 0.012 * (draw.random() - 0.5)
        paths[pair.base] = tuple(path)
    return paths


SPOTS = _spots()


def forward_price(base: str, spot: Decimal) -> Decimal:
    """``spot`` carried a month at the stated rates: ``spot x (1 + r$ / 12) / (1 + r / 12)``."""

    twelve = Decimal(12)
    carried = spot * (1 + RATES["USD"] / twelve) / (1 + RATES[base] / twelve)
    return carried.quantize(_FIVE, ROUND_HALF_EVEN)


def _quote(asset_id: str, at: float, mid: Decimal) -> Quote:
    return Quote(
        asset_id=asset_id,
        timestamp=at,
        bid=mid,
        ask=mid,
        bid_size=Decimal("100000000"),
        ask_size=Decimal("100000000"),
        venue="XFXV",
        currency="USD",
    )


def dataset() -> MarketDataset:
    """Each pair's spot and forward, quoted at every instant."""

    quotes = [
        _quote(record.asset_id, at, mid)
        for pair in PAIRS
        for at, spot in zip(INSTANTS, SPOTS[pair.base], strict=True)
        for record, mid in ((pair.spot, spot), (pair.forward, forward_price(pair.base, spot)))
    ]
    return MarketDataset.of("EX84-FX", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id)))


def schedule() -> RunSchedule:
    """The rates the book is valued at: each pair's spot at every instant, its source stated."""

    return RunSchedule.of(
        rates=[
            FxRate(pair.base, "USD", spot, at, "the example's synthetic spot")
            for pair in PAIRS
            for at, spot in zip(INSTANTS, SPOTS[pair.base], strict=True)
        ]
    )


# --------------------------------------------------------------------------- #
# The strategy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Roll:
    """One roll the strategy asked for: the pair, the day, and the two value dates."""

    base: str
    struck: date
    near: date
    far: date


class CarryRoller(BaseStrategy):
    """Holds ``NOTIONAL`` of each pair a month forward, and rolls it as a swap.

    It buys each forward at the pair's first quote. On the day the pair's spot
    value date reaches the open forward's value date, it asks for a package:
    the near leg sells the currency at spot, settling with the forward, and the
    far leg buys it a month on. Every value date it counts is its own, by the
    pair's rule over the calendars stated above; the run counts them again from
    the records, and they agree.
    """

    def __init__(self) -> None:
        #: Each pair's open forward, by its value date.
        self.held: dict[str, date] = {}
        #: Every roll asked for, in order.
        self.rolls: list[Roll] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quote = event.quote
        pair = BY_FORWARD.get(quote.asset_id)
        if pair is None:
            return ()
        at = quote.timestamp
        day = datetime.fromtimestamp(at, UTC).date()
        if pair.base not in self.held:
            self.held[pair.base] = forward_value_date(
                day, pair.rule, ONE_MONTH, CONVENTION, end_of_month=True
            )
            return (Intent(STRATEGY_ID, pair.forward.asset_id, NOTIONAL, timestamp=at),)
        maturing = self.held[pair.base]
        if spot_value_date(day, pair.rule) < maturing:
            return ()
        far = forward_value_date(day, pair.rule, ONE_MONTH, CONVENTION, end_of_month=True)
        self.held[pair.base] = far
        self.rolls.append(Roll(pair.base, day, spot_value_date(day, pair.rule), far))
        legs = (Leg(pair.spot.asset_id, Decimal("-1")), Leg(pair.forward.asset_id, Decimal("1")))
        roll = Intent(
            STRATEGY_ID,
            f"ROLL-{pair.base}",
            NOTIONAL,
            terms=MARKET,
            timestamp=at,
            kind=IntentKind.PACKAGE,
            legs=legs,
        )
        return (roll,)


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def run_config() -> RunConfig:
    """Capital, limits, costs and fills: each stated, none assumed."""

    ample = Decimal("1000000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX84", "USD", "Example 84", 1.0),
            starting_cash=START_CASH,
            # The allocation's ceiling is the gross notional the orders of one
            # instant may commit: both pairs' swaps can fall on one day.
            budget=CapitalBudget(
                global_capital=Decimal("10000000"),
                maximum_exposure=Decimal("10000000"),
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: Decimal("10000000")},
                currency="USD",
            ),
            # A pair's book in the allocation is what the strategy has dealt in
            # it, so each roll's near leg leaves the spot record's book short:
            # the run allows it. The account holds currencies, not pairs.
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=False
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(ample, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("1")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(ample, "UTC"),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            simulator=ExecutionSimulator(commission_model=FixedCommission(COMMISSION)),
            instruments=INSTRUMENTS,
            calendars=CALENDARS,
            also_settles=frozenset({"AUD", "NZD"}),
            fill_timing=FillTiming.SAME_EVENT,
        ),
        fill_policy=ImmediateFill(),
        seed=84,
        start_timestamp=INSTANTS[0] - 1.0,
    )


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def context_factory(strategy_id: str) -> StrategyContext:
    """The context the strategy sees: it reads nothing from it."""

    return StrategyContext(
        portfolio=NoPortfolio(),
        market=NoMarket(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=NoRiskView(),
        config={"strategy_id": strategy_id},
        orders=NoOrders(),
    )


def _running(strategy: CarryRoller) -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, strategy)
    entry = state.strategies[STRATEGY_ID]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(state, strategies={STRATEGY_ID: entry})


def closing_rates() -> FxRates:
    """The rates the finished book is valued at: each pair's last spot."""

    return FxRates.of(
        FxRate(pair.base, "USD", SPOTS[pair.base][-1], INSTANTS[-1], "the example's synthetic spot")
        for pair in PAIRS
    )


def run() -> tuple[BacktestResult, CarryRoller]:
    """The year's run, and the strategy that asked for it."""

    strategy = CarryRoller()
    result = BacktestEngine.run(
        run_config(), dataset(), _running(strategy), context_factory, schedule=schedule()
    )
    return result, strategy


# --------------------------------------------------------------------------- #
# Reading the result
# --------------------------------------------------------------------------- #

#: Each record's two currencies, the base first.
CURRENCIES = {
    record.asset_id: (pair.base, "USD") for pair in PAIRS for record in (pair.spot, pair.forward)
}


def tally(result: BacktestResult) -> dict[str, Decimal]:
    """Each currency's cash by hand, from the fills alone: the dollars it started with,
    each deal's two amounts, and each fill's commission."""

    cash = {"USD": START_CASH, **{pair.base: Decimal("0") for pair in PAIRS}}
    for fill in result.fills:
        base, quote = CURRENCIES[fill.asset_id]
        sign = Decimal("1") if fill.side.value == "buy" else Decimal("-1")
        cash[base] += sign * fill.quantity
        cash[quote] -= sign * fill.quantity * fill.price + COMMISSION
    return cash


def unadjusted(day: date, pair: Pair) -> date:
    """The forward's value date had no market a holiday: weekends only, by the same rule."""

    weekdays = _weekdays("WEEKDAYS", "UTC", ())
    rule = ValueDateRule(pair.rule.spot_lag, (weekdays,), (weekdays,))
    return forward_value_date(day, rule, ONE_MONTH, CONVENTION, end_of_month=True)
