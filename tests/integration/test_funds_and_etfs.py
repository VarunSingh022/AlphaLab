"""Funds and ETFs as booking classes (v4.3.0, ADR-0076, GAP-51).

By hand, in New York's zone, a fund dealt on ``XFND`` from 09:00 to 16:00 each
weekday, its NAV published at 18:00:

* **An ETF is a share.** Ten bought on Monday 6 January 2025 at 10:00 fill at
  that quote, 500.00, as a share's order does.
* **A fund deals at its NAV.** A hundred units asked for at 10:00 on Monday are
  dealt at Monday's NAV, 10.20, published at 18:00 after the 16:00 cutoff --
  not at Friday's 10.00, the last price when the order was placed. Fifty asked
  for at 17:00, after the cutoff, are dealt at Tuesday's NAV, 10.30.
* **Never sold short.** On Tuesday at 10:00 the book holds a hundred units, the
  fifty not yet dealt: a redemption of two hundred is refused.
* **What a fund's order may not be**: a day order, a limit order, a package's
  leg, and an order in a fund whose venue declares no calendar -- each refused
  with the reason.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from alphalab.backtesting import BacktestEngine, MarketDataset
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.core.enums import AssetType, OrderType
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.instrument.economics import economics_for
from alphalab.instrument.record import InstrumentRecord
from alphalab.runtime.calendars import VenueCalendars
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind, Leg, PackageRefused
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    backtest_config,
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
)

D = Decimal
NY = ZoneInfo("America/New_York")
DEALING = MarketCalendar(
    "XFND", "America/New_York", dict.fromkeys(range(5), (SessionWindow(time(9), time(16)),))
)
FUND = InstrumentRecord("GLOBAL", AssetType.FUND, "XFND", "USD")
ETF = InstrumentRecord("SPYX", AssetType.ETF, "XNYS", "USD")
GTC = OrderTerms(time_in_force=TimeInForce.GTC)


def ny(day: int, hour: int) -> float:
    return datetime(2025, 1, day, hour, tzinfo=NY).timestamp()


FRIDAY_NAV, MON_10, MON_17, MON_NAV, TUE_10, TUE_NAV = (
    ny(3, 18),
    ny(6, 10),
    ny(6, 17),
    ny(6, 18),
    ny(7, 10),
    ny(7, 18),
)


class Investor(BaseStrategy):
    """Asks for what its plan says at each ETF quote, and keeps each rejection's reason."""

    def __init__(self, plan: dict[float, tuple[Intent, ...]]) -> None:
        self._plan = plan
        self.rejections: list[str] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.asset_id != ETF.asset_id:
            return ()
        return self._plan.get(event.quote.timestamp, ())

    def on_order(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.reason:
            self.rejections.append(event.reason)
        return ()


def asks(asset: InstrumentRecord, quantity: str, at: float, terms: OrderTerms = GTC) -> Intent:
    return Intent("S", asset.asset_id, D(quantity), terms=terms, timestamp=at)


def run(plan: dict[float, tuple[Intent, ...]], calendars: VenueCalendars | None = None) -> Any:
    base = backtest_config("S")
    pipeline = replace(
        base.pipeline,
        instruments=registry_of(FUND, ETF),
        calendars=VenueCalendars({"XFND": DEALING}) if calendars is None else calendars,
    )
    marks = [
        (FUND, FRIDAY_NAV, "10.00"),
        (ETF, MON_10, "500.00"),
        (ETF, MON_17, "501.00"),
        (FUND, MON_NAV, "10.20"),
        (ETF, TUE_10, "502.00"),
        (FUND, TUE_NAV, "10.30"),
    ]
    investor = Investor(plan)
    result = BacktestEngine.run(
        replace(base, pipeline=pipeline),
        MarketDataset.of("FUNDS", [quote(a.asset_id, at, D(mid)) for a, at, mid in marks]),
        running_strategy_state("S", investor),
        context_factory,
    )
    return result, investor


def test_both_book_as_fully_paid_units() -> None:
    assert economics_for(AssetType.ETF, None).is_cash_equity
    assert economics_for(AssetType.FUND, None).is_cash_equity


def test_an_etf_is_a_share_and_a_fund_deals_at_the_nav_after_its_cutoff() -> None:
    plan = {
        MON_10: (asks(ETF, "10", MON_10, OrderTerms()), asks(FUND, "100", MON_10)),
        MON_17: (asks(FUND, "50", MON_17),),
        TUE_10: (asks(FUND, "-200", TUE_10),),
    }
    result, investor = run(plan)
    fills = [(f.asset_id, f.quantity, f.price, f.filled_at) for f in result.fills]
    assert fills == [
        (ETF.asset_id, D("10"), D("500.00"), MON_10),
        (FUND.asset_id, D("100"), D("10.20"), MON_NAV),
        (FUND.asset_id, D("50"), D("10.30"), TUE_NAV),
    ]
    (reason,) = investor.rejections
    assert "200" in reason and "never sold short" in reason
    assert result.state.portfolio.positions[FUND.asset_id].quantity == D("150")


@pytest.mark.parametrize(
    ("terms", "reason"),
    [
        (OrderTerms(), "a market order, good till cancelled"),
        (
            OrderTerms(OrderType.LIMIT, limit_price=D("10.50"), time_in_force=TimeInForce.GTC),
            "a market order, good till cancelled",
        ),
    ],
    ids=["day", "limit"],
)
def test_a_funds_order_is_a_market_order_good_till_cancelled(
    terms: OrderTerms, reason: str
) -> None:
    _, investor = run({MON_10: (asks(FUND, "100", MON_10, terms),)})
    (said,) = investor.rejections
    assert reason in said


def test_a_fund_is_no_packages_leg_and_needs_its_dealing_calendar() -> None:
    package = Intent(
        "S",
        "PAIR",
        D("10"),
        terms=OrderTerms(),
        timestamp=MON_10,
        kind=IntentKind.PACKAGE,
        legs=(Leg(FUND.asset_id, D("1")), Leg(ETF.asset_id, D("-1"))),
    )
    result, _ = run({MON_10: (package,)})
    (refused,) = [e for e in result.state.strategy.events if isinstance(e, PackageRefused)]
    assert "dealt once at its NAV" in refused.reason
    _, investor = run({MON_10: (asks(FUND, "100", MON_10),)}, calendars=VenueCalendars())
    (said,) = investor.rejections
    assert "no calendar is declared for XFND" in said
