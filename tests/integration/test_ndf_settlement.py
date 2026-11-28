"""An NDF in a run: dealt, fixed at a supplied rate, settled at its value date (v4.3.0, ADR-0067).

By hand. A one-month USD/INR NDF settled in dollars is bought on Wednesday
2 July 2025 at 83.50. Two Mumbai business days reach Friday 4 July, a US
holiday, so spot is Monday 7 July and the value date Thursday 7 August. Two
business days of both calendars before it, it fixes on Tuesday 5 August. The
run's schedule supplies the fixing, 84.00 from the source the deal names:

* the trade moves nothing but its 500 INR commission;
* the fixing owes the buyer 1,000,000 x 0.50 / 84.00 = 5,952.38 USD, realized,
  pending until 7 August's open and settled after.

Refused, and the run stops with the reason: a fixing from another source, of
another day, of the inverted pair, or supplied before it is true; and a deal
that reaches its value date with no fixing at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    Funding,
    MarketDataset,
    RunSchedule,
    ScheduledFixing,
)
from alphalab.conventions.business_days import BusinessDayConvention, Tenor, TenorUnit
from alphalab.core.enums import AssetType
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.execution.commission import FixedCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.terms import FxKind, FxTerms, FxValueDateRule, NdfFixing
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.portfolio.events import NdfFixed
from alphalab.portfolio.fx import FxRate
from alphalab.portfolio.settlement import NDF_FIXES_ON, NDF_FIXING, VALUE_DATE, pending_from
from alphalab.portfolio.types import TransactionType
from alphalab.runtime.calendars import VenueCalendars
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    backtest_config,
    context_factory,
    permissive_risk_limits,
    quote,
    registry_of,
    running_strategy_state,
)

D = Decimal
CASH = D("2000000")
SOURCE = "the INR reference rate"


def utc(day: int, month: int, hour: int = 14) -> float:
    return datetime(2025, month, day, hour, tzinfo=UTC).timestamp()


def weekdays(calendar_id: str, zone: str, *holidays: date) -> MarketCalendar:
    sessions = dict.fromkeys(range(5), (SessionWindow(time(0, 0), time(23, 0)),))
    return MarketCalendar(calendar_id, zone, sessions, frozenset(holidays))


VENUE = weekdays("XFXV", "UTC")
USNY = weekdays("USNY", "America/New_York", date(2025, 7, 4))
XBOM = weekdays("XBOM", "Asia/Kolkata", date(2025, 8, 15))
CALENDARS = VenueCalendars({"XFXV": VENUE, "USNY": USNY, "XBOM": XBOM})
NDF = InstrumentRecord(
    "USDINR1M",
    AssetType.FOREX,
    "XFXV",
    "INR",
    terms=FxTerms(
        "USD",
        "INR",
        FxKind.NDF,
        FxValueDateRule(
            2,
            ("XBOM",),
            ("USNY", "XBOM"),
            tenor=Tenor(1, TenorUnit.MONTHS),
            convention=BusinessDayConvention.MODIFIED_FOLLOWING,
            end_of_month=False,
        ),
        NdfFixing("USD", 2, SOURCE),
    ),
)
WED, FIXING_AT, AFTER = utc(2, 7), utc(5, 8, 12), utc(8, 8)
VALUE_OPEN = datetime(2025, 8, 7, tzinfo=UTC).timestamp()
THE_FIXING = FxRate("USD", "INR", D("84.00"), FIXING_AT, SOURCE)


class Buyer(BaseStrategy):
    """Buys the NDF at its first quote; stateless."""

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.timestamp != WED:
            return ()
        return (Intent("S", NDF.asset_id, D("1000000"), timestamp=WED),)


def run(*fixings: ScheduledFixing) -> BacktestResult:
    base = backtest_config(
        "S",
        starting_cash=CASH,
        simulator=ExecutionSimulator(commission_model=FixedCommission(D("500"))),
        risk_limits=permissive_risk_limits(D("10000000")),
    )
    pipeline = replace(
        base.pipeline,
        budget=base.pipeline.budget.in_currency("USD"),
        instruments=registry_of(NDF),
        calendars=CALENDARS,
        also_settles=frozenset({"INR"}),
    )
    marks = [(WED, "83.50"), (utc(5, 8, 13), "84.00"), (AFTER, "84.10")]
    return BacktestEngine.run(
        replace(base, pipeline=pipeline),
        MarketDataset.of("NDF", [quote(NDF.asset_id, at, D(mid)) for at, mid in marks]),
        running_strategy_state("S", Buyer()),
        context_factory,
        schedule=RunSchedule.of(
            rates=[FxRate("INR", "USD", D("0.012"), 1.0, "test")],
            fundings=[Funding(D("100000"), "INR", 1.5)],
            fixings=fixings,
        ),
    )


def test_an_ndf_is_dealt_fixed_and_settled_by_hand() -> None:
    result = run(ScheduledFixing(NDF.asset_id, THE_FIXING, FIXING_AT))
    portfolio = result.state.portfolio
    deal, fixed = [t for t in portfolio.ledger.transactions if t.type is TransactionType.FX_LEG]
    # The trade: no currency but the commission, and the dates its fixing needs.
    assert (deal.quantity, deal.commission, deal.currency) == (D("0"), D("500.00"), "INR")
    assert (deal.metadata[NDF_FIXES_ON], deal.metadata[VALUE_DATE]) == ("2025-08-05", "2025-08-07")
    # The fixing: 1,000,000 x (84.00 - 83.50) / 84.00 dollars, owed to the buyer.
    assert (fixed.quantity, fixed.currency, fixed.metadata[NDF_FIXING]) == (
        D("5952.38"),
        "USD",
        "84.00",
    )
    assert portfolio.cash.balance("USD") == CASH + D("5952.38")
    assert portfolio.cash.balance("INR") == D("100000") - D("500.00")
    assert portfolio.realized_pnl.of("USD") == D("5952.38")
    assert portfolio.positions == {}
    (event,) = [e for e in portfolio.events if isinstance(e, NdfFixed)]
    assert (event.amount, event.source, event.fixing_as_of) == (D("5952.38"), SOURCE, FIXING_AT)
    # Pending until the value date's open, settled after.
    dollar = PortfolioEngine.cash_position(portfolio, "USD", VALUE_OPEN - 1)
    assert dollar.unsettled_credits == D("5952.38") and dollar.settled == CASH
    assert PortfolioEngine.cash_position(portfolio, "USD", VALUE_OPEN).settled == (
        CASH + D("5952.38")
    )
    assert pending_from(portfolio.ledger.transactions) == portfolio.unsettled


@pytest.mark.parametrize(
    ("fixing", "reason"),
    [
        (replace(THE_FIXING, source="another source"), "from the source the deal names"),
        (replace(THE_FIXING, as_of=utc(4, 8, 12)), "fixes on 2025-08-04"),
        (FxRate("INR", "USD", D("0.0119"), FIXING_AT, SOURCE), "never inverted"),
        (replace(THE_FIXING, as_of=utc(5, 8, 13)), "not known before it is made"),
    ],
    ids=["source", "day", "inverted", "future-dated"],
)
def test_a_fixing_the_deal_cannot_be_settled_by_stops_the_run(fixing: FxRate, reason: str) -> None:
    with pytest.raises(RuntimeValidationError, match=reason):
        run(ScheduledFixing(NDF.asset_id, fixing, FIXING_AT))


def test_a_deal_that_reaches_its_value_date_unfixed_stops_the_run() -> None:
    with pytest.raises(RuntimeValidationError, match="with no fixing booked"):
        run()
