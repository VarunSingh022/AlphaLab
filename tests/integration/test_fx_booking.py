"""An FX deal in a run: two currencies booked, value dates by both calendars (v4.3.0, ADR-0067).

GR-3's booking, by hand. A EUR/USD forward three months from spot is struck on
Wednesday 2 July 2025. Two TARGET business days reach Friday 4 July, a US
holiday, so spot rolls to Monday 7 July, and three months later is Tuesday
7 October, open in both calendars. The run books the euros and the dollars and
no position:

* cash in each currency equals the hand calculation, commission included;
* both legs are pending until 7 October's open in the venue's calendar, and
  settled after;
* the book is valued in dollars at the supplied rate, and nothing else
  converts it.

A spot deal struck the same day is valued 7 July. Refused before an order
exists:

* a deal the currency it pays cannot cover by its value date -- euros sold that
  the account does not hold -- though a credit arriving by that date counts, so
  euros a forward delivers on 7 October pay a sale valued that day;
* a pair whose base the run does not settle;
* a calendar the rule names that the run does not declare;
* an NDF settling its difference in a currency the run does not settle (its
  fixing is ``test_ndf_settlement.py``'s).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from alphalab.backtesting import BacktestEngine, BacktestResult, MarketDataset, RunSchedule
from alphalab.common.order_terms import MARKET
from alphalab.conventions.business_days import BusinessDayConvention, Tenor, TenorUnit
from alphalab.core.enums import AssetType
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.execution.commission import FixedCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.terms import FxKind, FxTerms, FxValueDateRule, NdfFixing
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.portfolio.events import FxDealBooked
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.portfolio.settlement import SETTLES_AT, VALUE_DATE, pending_from
from alphalab.portfolio.types import TransactionType
from alphalab.runtime.calendars import VenueCalendars
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineResult,
    ExecutionPipelineState,
)
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind, Leg, PackageRefused
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    backtest_config,
    context_factory,
    permissive_risk_limits,
    pipeline_config,
    quote,
    registry_of,
    running_strategy_state,
)

D = Decimal
CASH = D("2000000")


def utc(day: int, month: int, hour: int = 14) -> float:
    return datetime(2025, month, day, hour, tzinfo=UTC).timestamp()


def weekdays(calendar_id: str, zone: str, *holidays: date) -> MarketCalendar:
    sessions = dict.fromkeys(range(5), (SessionWindow(time(0, 0), time(23, 0)),))
    return MarketCalendar(calendar_id, zone, sessions, frozenset(holidays))


#: The venue the pairs are dealt on, open every weekday; and each currency's.
VENUE = weekdays("XFXV", "UTC")
TARGET = weekdays("TARGET", "Europe/Berlin", date(2025, 12, 25), date(2025, 12, 26))
USNY = weekdays("USNY", "America/New_York", date(2025, 7, 4), date(2025, 11, 27))
CALENDARS = VenueCalendars({"XFXV": VENUE, "TARGET": TARGET, "USNY": USNY})

#: EUR/USD's common rule: two days counted in TARGET, settled where both are open.
SPOT_RULE = FxValueDateRule(2, ("TARGET",), ("TARGET", "USNY"))
THREE_MONTHS = replace(
    SPOT_RULE,
    tenor=Tenor(3, TenorUnit.MONTHS),
    convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    end_of_month=True,
)
SPOT = InstrumentRecord(
    "EURUSD", AssetType.FOREX, "XFXV", "USD", terms=FxTerms("EUR", "USD", FxKind.SPOT, SPOT_RULE)
)
FORWARD = InstrumentRecord(
    "EURUSD3M",
    AssetType.FOREX,
    "XFXV",
    "USD",
    terms=FxTerms("EUR", "USD", FxKind.FORWARD, THREE_MONTHS),
)
#: A EUR/USD NDF settled in euros, in a run that settles dollars alone.
NDF = InstrumentRecord(
    "EURUSDNDF1M",
    AssetType.FOREX,
    "XFXV",
    "USD",
    terms=FxTerms(
        "EUR",
        "USD",
        FxKind.NDF,
        replace(THREE_MONTHS, tenor=Tenor(1, TenorUnit.MONTHS)),
        NdfFixing("EUR", 2, "the run's EUR reference rate"),
    ),
)
WED = utc(2, 7)
RATES = FxRates.of([FxRate("EUR", "USD", D("1.0900"), WED, "test")])


class Trader(BaseStrategy):
    """Deals a scripted amount at chosen quotes, and keeps each rejection's reason."""

    def __init__(self, plan: dict[tuple[float, str], Decimal]) -> None:
        self._plan = plan
        self.rejections: list[str] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        wanted = self._plan.get((event.quote.timestamp, event.quote.asset_id))
        if wanted is None:
            return ()
        return (Intent("S", event.quote.asset_id, wanted, timestamp=event.quote.timestamp),)

    def on_order(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.reason:
            self.rejections.append(event.reason)
        return ()


def pipeline(
    trader: Trader,
    *records: InstrumentRecord,
    settles: frozenset[str] = frozenset({"EUR"}),
    calendars: VenueCalendars = CALENDARS,
) -> ExecutionPipelineState:
    base = pipeline_config(
        "S",
        CASH,
        ExecutionSimulator(commission_model=FixedCommission(D("25.00"))),
        permissive_risk_limits(D("10000000")),
    )
    config = replace(
        base,
        budget=base.budget.in_currency("USD"),
        instruments=registry_of(*records),
        calendars=calendars,
        also_settles=settles,
    )
    return ExecutionPipeline.initialize(config, running_strategy_state("S", trader), 1.0)


def deal(
    record: InstrumentRecord, quantity: str, mid: str, **kw: Any
) -> tuple[ExecutionPipelineResult, Trader]:
    trader = Trader({(WED, record.asset_id): D(quantity)})
    state = pipeline(trader, record, **kw)
    priced = quote(record.asset_id, WED, D(mid))
    return ExecutionPipeline.process_quote(state, priced, context_factory, rates=RATES), trader


def legs(result: ExecutionPipelineResult | BacktestResult) -> list[Any]:
    return [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.FX_LEG
    ]


def test_a_forward_books_two_currencies_valued_three_months_from_a_holiday_moved_spot() -> None:
    result, _ = deal(FORWARD, "1000000", "1.0912")
    portfolio = result.state.portfolio

    # By hand: 1,000,000 EUR credited; 1,000,000 x 1.0912 = 1,091,200.00 USD and
    # the 25.00 commission debited. No position, nothing realized.
    assert portfolio.cash.balance("EUR") == D("1000000.00")
    assert portfolio.cash.balance("USD") == CASH - D("1091200.00") - D("25.00")
    assert portfolio.positions == {}
    assert portfolio.realized_pnl.of("USD") == 0
    (record,) = result.state.trade_records
    assert record.realized_pnl == 0 and record.holding_period_seconds is None

    # Spot is Monday 7 July, the Fourth having moved it; three months on is
    # Tuesday 7 October, and the legs settle at its open in the venue's calendar.
    value_open = datetime(2025, 10, 7, tzinfo=UTC).timestamp()
    base, quote_leg = legs(result)
    for leg in (base, quote_leg):
        assert leg.metadata[VALUE_DATE] == "2025-10-07"
        assert leg.metadata[SETTLES_AT] == value_open
    (event,) = [e for e in portfolio.events if isinstance(e, FxDealBooked)]
    assert (event.base_amount, event.quote_amount, event.commission) == (
        D("1000000.00"),
        D("-1091200.00"),
        D("25.00"),
    )

    # Pending until then, settled after.
    euro = PortfolioEngine.cash_position(portfolio, "EUR", value_open - 1)
    assert (euro.unsettled_credits, euro.settled, euro.available) == (
        D("1000000.00"),
        D("0"),
        D("0"),
    )
    dollar = PortfolioEngine.cash_position(portfolio, "USD", value_open - 1)
    assert dollar.unsettled_debits == D("1091225.00") and dollar.settled == CASH
    for currency in ("EUR", "USD"):
        after = PortfolioEngine.cash_position(portfolio, currency, value_open)
        assert after.settled == after.trade_date == portfolio.cash.balance(currency)
    assert pending_from(portfolio.ledger.transactions) == portfolio.unsettled

    # Valued in dollars at the supplied rate: the forward is worth its two
    # amounts converted, undiscounted.
    valuation = result.valuation
    assert valuation is not None
    assert valuation.equity == (CASH - D("1091225.00")) + D("1000000.00") * D("1.0900")


def test_a_spot_deal_is_valued_at_spot_and_a_sale_of_what_is_not_held_is_refused() -> None:
    bought, _ = deal(SPOT, "500000", "1.0850")
    assert len(legs(bought)) == 2
    for leg in legs(bought):
        assert leg.metadata[VALUE_DATE] == "2025-07-07"
        assert leg.metadata[SETTLES_AT] == datetime(2025, 7, 7, tzinfo=UTC).timestamp()
    # Euros the account does not hold cannot be delivered: refused before an
    # order exists, as every purchase its currency's cash cannot pay is (ACC-017).
    sold, trader = deal(SPOT, "-500000", "1.0850")
    assert legs(sold) == [] and not list(sold.state.oms.orders.orders())
    (reason,) = trader.rejections
    assert "pays 500000" in reason and "by the deal's value date" in reason


def test_a_pair_whose_base_the_run_does_not_settle_is_refused_before_an_order_exists() -> None:
    result, _ = deal(FORWARD, "1000000", "1.0912", settles=frozenset())
    (refusal,) = result.settlement_refusals
    assert "does not settle EUR" in refusal.detail and "also_settles" in refusal.detail
    assert legs(result) == [] and not list(result.state.oms.orders.orders())


def test_a_calendar_the_rule_names_and_the_run_does_not_declare_is_refused() -> None:
    result, trader = deal(
        FORWARD, "1000000", "1.0912", calendars=VenueCalendars({"XFXV": VENUE, "TARGET": TARGET})
    )
    assert legs(result) == []
    (reason,) = trader.rejections
    assert "USNY" in reason and "none is assumed open" in reason


def test_an_ndf_settled_in_a_currency_the_run_does_not_settle_is_refused() -> None:
    result, _ = deal(NDF, "1000", "1.0912", settles=frozenset())
    (refusal,) = result.settlement_refusals
    assert "settles the difference from its fixing in EUR" in refusal.detail
    assert legs(result) == []


def _played(
    plan: dict[tuple[float, str], Decimal], marks: list[tuple[InstrumentRecord, float, str]]
) -> tuple[ExecutionPipelineResult, Trader]:
    trader = Trader(plan)
    state = pipeline(trader, SPOT, FORWARD)
    result = None
    for record, at, mid in marks:
        result = ExecutionPipeline.process_quote(
            state, quote(record.asset_id, at, D(mid)), context_factory, rates=RATES
        )
        state = result.state
    assert result is not None
    return result, trader


def test_euros_a_forward_delivers_pay_a_sale_valued_the_same_day_and_no_earlier_one() -> None:
    # Bought forward on 2 July for 7 October. Struck Friday 3 October, a spot
    # sale is valued 7 October too: the forward's euros arrive that day and pay
    # it. Struck Monday 29 September, one is valued 1 October, before they do.
    friday, monday = utc(3, 10), utc(29, 9)
    for struck, sold in ((friday, True), (monday, False)):
        plan = {(WED, FORWARD.asset_id): D("1000000"), (struck, SPOT.asset_id): D("-1000000")}
        result, trader = _played(plan, [(FORWARD, WED, "1.0912"), (SPOT, struck, "1.1000")])
        euros = result.state.portfolio.cash.balance("EUR")
        if sold:
            assert euros == D("0.00") and trader.rejections == []
            near = [leg for leg in legs(result) if leg.timestamp == struck]
            assert {leg.metadata[VALUE_DATE] for leg in near} == {"2025-10-07"}
        else:
            assert euros == D("1000000.00")
            (reason,) = trader.rejections
            assert "pays 1000000" in reason and "EUR" in reason


# --------------------------------------------------------------------------- #
# A rollover, as a swap (GR-3)
# --------------------------------------------------------------------------- #

#: Friday 3 October: spot is Tuesday 7 October, the forward's own value date.
FRI = utc(3, 10)
NEAR_FAR = (Leg(SPOT.asset_id, D("-1")), Leg(FORWARD.asset_id, D("1")))


class Roller(BaseStrategy):
    """Buys the forward on 2 July; on 3 October, once both legs are priced, rolls it as a swap."""

    def __init__(self, legs: tuple[Leg, ...]) -> None:
        self._legs = legs

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quote_ = event.quote
        if (quote_.timestamp, quote_.asset_id) == (WED, FORWARD.asset_id):
            return (Intent("S", FORWARD.asset_id, D("1000000"), timestamp=WED),)
        if quote_.timestamp == FRI and quote_.asset_id == max(SPOT.asset_id, FORWARD.asset_id):
            roll = Intent(
                "S",
                "ROLL",
                D("1000000"),
                terms=MARKET,
                timestamp=FRI,
                kind=IntentKind.PACKAGE,
                legs=self._legs,
            )
            return (roll,)
        return ()


def rolled(legs: tuple[Leg, ...] = NEAR_FAR) -> BacktestResult:
    base = backtest_config(
        "S",
        starting_cash=CASH,
        simulator=ExecutionSimulator(commission_model=FixedCommission(D("25.00"))),
        risk_limits=permissive_risk_limits(D("10000000")),
    )
    # The allocation's ceiling is the gross notional a batch may commit; the
    # swap's two legs commit 2.2 million together.
    budget = replace(
        base.pipeline.budget.in_currency("USD"),
        global_capital=D("5000000"),
        maximum_exposure=D("50000000"),
        strategy_budgets={"S": D("5000000")},
    )
    pipeline = replace(
        base.pipeline,
        budget=budget,
        instruments=registry_of(SPOT, FORWARD),
        calendars=CALENDARS,
        also_settles=frozenset({"EUR"}),
    )
    quotes = [
        quote(FORWARD.asset_id, WED, D("1.0912")),
        quote(SPOT.asset_id, FRI, D("1.1000")),
        quote(FORWARD.asset_id, FRI, D("1.1040")),
    ]
    return BacktestEngine.run(
        replace(base, pipeline=pipeline),
        MarketDataset.of("ROLL", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id))),
        running_strategy_state("S", Roller(legs)),
        context_factory,
        schedule=RunSchedule.of(rates=[FxRate("EUR", "USD", D("1.0900"), WED, "test")]),
    )


def test_a_forward_rolled_as_a_swap_moves_each_currency_as_its_legs_say() -> None:
    result = rolled()
    portfolio = result.state.portfolio
    assert not [e for e in result.state.strategy.events if isinstance(e, PackageRefused)]
    # Bought forward at 1.0912; at the roll, the near leg sells the euros at spot
    # for 7 October, 1.1000, and the far leg buys them back three months on,
    # 1.1040 for 7 January. Each fill pays 25.00.
    assert [(f.asset_id, f.side.value, f.quantity, f.price) for f in result.fills] == [
        (FORWARD.asset_id, "buy", D("1000000"), D("1.0912")),
        (SPOT.asset_id, "sell", D("1000000"), D("1.1000")),
        (FORWARD.asset_id, "buy", D("1000000"), D("1.1040")),
    ]
    assert portfolio.cash.balance("EUR") == D("1000000.00")
    assert portfolio.cash.balance("USD") == (
        CASH - D("1091225.00") + D("1099975.00") - D("1104025.00")
    )
    assert portfolio.positions == {}
    value_dates = [leg.metadata[VALUE_DATE] for leg in legs(result)]
    assert value_dates == ["2025-10-07"] * 4 + ["2026-01-07"] * 2

    # The far leg cost more dollars than the book held after the first forward
    # (908,775.00): the near leg's proceeds, arriving 7 October, paid it.
    october, january = (
        datetime(2025, 10, 7, tzinfo=UTC).timestamp(),
        datetime(2026, 1, 7, tzinfo=UTC).timestamp(),
    )
    euro = PortfolioEngine.cash_position(portfolio, "EUR", october - 1)
    assert (euro.unsettled_credits, euro.unsettled_debits) == (D("2000000.00"), D("1000000.00"))
    # On 7 October the first forward and the near leg settle together: the
    # euros arrive and leave, and the roll's gain arrives in dollars.
    euro, dollar = (PortfolioEngine.cash_position(portfolio, c, october) for c in ("EUR", "USD"))
    assert euro.settled == D("0.00")
    assert dollar.settled == CASH - D("1091225.00") + D("1099975.00")
    # On 7 January the far leg settles: the euros are held again.
    euro = PortfolioEngine.cash_position(portfolio, "EUR", january)
    assert euro.settled == euro.trade_date == D("1000000.00")
    assert pending_from(portfolio.ledger.transactions) == portfolio.unsettled


def test_a_swap_settles_its_near_leg_first_whatever_order_it_names_them_in() -> None:
    forward_first = rolled((NEAR_FAR[1], NEAR_FAR[0]))
    assert [f.asset_id for f in forward_first.fills] == [
        FORWARD.asset_id,
        SPOT.asset_id,
        FORWARD.asset_id,
    ]
    assert forward_first.state.portfolio.cash.balances == rolled().state.portfolio.cash.balances
