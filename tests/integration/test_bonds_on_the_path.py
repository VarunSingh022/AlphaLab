"""A fixed-rate bond on the execution path: coupons and redemption from its terms (v4.1.1, FUT-010).

``AssetType.BOND`` declares economics as a future does -- a unit's multiplier
on the quoted price -- and its maturity as its last trading instant.
``alphalab.api.bond_schedule`` turns a :class:`~alphalab.macro.bond.Bond`'s
terms into per-unit coupons and a redemption at par, which the run applies at
their instants to whoever holds. Trading at full (dirty) prices carries the
accrual in the price. Each figure is checked against the bond's own arithmetic.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from alphalab.api import bond_schedule
from alphalab.backtesting import BacktestEngine, BacktestResult, ScheduledDistribution
from alphalab.backtesting.dataset import MarketDataset
from alphalab.conventions.daycount import DayCount
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.rates import Compounding
from alphalab.core.enums import AssetType
from alphalab.data import DataValidationError
from alphalab.instrument.economics import economics_for
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import InstrumentRecord
from alphalab.macro.bond import Bond, cash_flows
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    START_CASH,
    backtest_config,
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
)

STRATEGY = "BONDS"
BOND = Bond(
    face=Decimal("1000"),
    coupon_rate=0.05,
    frequency=Compounding.SEMI_ANNUAL,
    issue_date=date(2024, 1, 15),
    maturity=date(2025, 7, 15),
    day_count=DayCount.THIRTY_360_US,
    currency="USD",
)


def at(day: date) -> float:
    """A flow is fixed at midnight UTC of its date."""

    return datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()


MATURITY = at(BOND.maturity)
NOTE = InstrumentRecord(
    "UST25",
    AssetType.BOND,
    "XGOV",
    "USD",
    # Quoted per 100 of face; one unit is one bond of 1,000 face.
    economics=InstrumentEconomics(Decimal("10"), SettlementModel.CASH_EQUITY),
    last_trade_at=MATURITY,
)
DAY = 86_400.0
#: Full (dirty) prices per 100 of face, one a month from February 2024.
PRICES = {at(date(2024, 2, 1)) + n * 30 * DAY: "101.00" for n in range(17)}
FIRST = min(PRICES)


class Buyer(BaseStrategy):
    def __init__(self, quantity: Decimal) -> None:
        self._quantity = quantity

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.timestamp != FIRST:
            return ()
        return (
            Intent(
                STRATEGY,
                NOTE.asset_id,
                self._quantity,
                timestamp=FIRST,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


def _run(quantity: Decimal = Decimal("10")) -> BacktestResult:
    base = backtest_config(STRATEGY)
    config = replace(base, pipeline=replace(base.pipeline, instruments=registry_of(NOTE)))
    records = [quote(NOTE.asset_id, ts, Decimal(px)) for ts, px in sorted(PRICES.items())]
    # A last price after maturity: the bond no longer trades, and the record is skipped.
    records.append(quote(NOTE.asset_id, MATURITY + DAY, Decimal("100")))
    return BacktestEngine.run(
        config,
        MarketDataset.of("BONDS", records),
        running_strategy_state(STRATEGY, Buyer(quantity)),
        context_factory,
        schedule=bond_schedule(BOND, NOTE, at, redemption_price=Decimal("100")),
    )


def test_the_schedule_is_the_bonds_coupons_and_its_redemption() -> None:
    schedule = bond_schedule(BOND, NOTE, at, redemption_price=Decimal("100"))
    coupons = [e.item for e in schedule.entries if isinstance(e.item, ScheduledDistribution)]
    assert [c.at for c in coupons] == [at(flow.payment_date) for flow in cash_flows(BOND)]
    assert all(c.per_unit == Decimal("25.00") for c in coupons)
    assert schedule.entries[-1].item.settlement.price == Decimal("100")
    assert schedule.entries[-1].at == MATURITY


def test_coupons_and_redemption_reach_the_holder_and_the_books_balance() -> None:
    result = _run()
    held_coupons = [flow for flow in cash_flows(BOND) if at(flow.payment_date) > FIRST]
    coupons = Decimal("10") * Decimal("25.00") * len(held_coupons)
    # Bought 10 bonds at 101 per 100 (1,010 each); redeemed at 100 (1,000 each).
    capital = Decimal("10") * (Decimal("100") - Decimal("101")) * Decimal("10")

    assert NOTE.asset_id not in result.state.portfolio.positions
    assert result.valuation.realized_pnl == coupons + capital
    assert result.state.portfolio.cash.balance("USD") == START_CASH + coupons + capital
    valuation = result.valuation
    assert valuation.equity == (
        START_CASH + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )
    assert [s.record.timestamp for s in result.run.skipped] == [MATURITY + DAY]


def test_a_short_pays_the_coupons() -> None:
    long = _run(Decimal("10")).valuation.realized_pnl
    short = _run(Decimal("-10")).valuation.realized_pnl
    assert long == -short


def test_a_bond_that_declares_no_economics_cannot_be_booked() -> None:
    with pytest.raises(InstrumentInputError, match="declares no economics"):
        economics_for(AssetType.BOND, None, "UST25")


@pytest.mark.parametrize(
    ("instrument", "message"),
    [
        (replace(NOTE, asset_type=AssetType.EQUITY), "is a equity"),
        (replace(NOTE, last_trade_at=None), "must stop trading"),
        (replace(NOTE, last_trade_at=MATURITY + DAY), "must stop trading"),
        (replace(NOTE, currency="EUR"), "trades in EUR"),
    ],
    ids=["not-a-bond", "no-maturity", "trades-past-maturity", "other-currency"],
)
def test_a_schedule_for_the_wrong_instrument_is_refused(
    instrument: InstrumentRecord, message: str
) -> None:
    with pytest.raises(DataValidationError, match=message):
        bond_schedule(BOND, instrument, at, redemption_price=Decimal("100"))
