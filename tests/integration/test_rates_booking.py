"""A rates book through a backtest, its fixings published on the schedule (v4.3, ADR-0065 s. 5).

By hand, on a weekday calendar, every payment booked at 17:00 in New York:

* **A receiver swap**: 50,000 notional of a one-year swap from Friday 17
  January 2025, receiving 4% semi-annual 30/360 and paying three-month SOFR
  quarterly at ACT/360, each fixed two business days before its period at
  4.30%, 4.20%, 4.10% and 4.00%. Bought at 0.0005 a unit. On 17 July the
  fixed and floating coupons are netted, as a swap's payments are. On 19
  January 2026 (the 17th is a Saturday) its last coupon is paid and the
  position is closed at nothing.
* **An FRA** on the three months from 17 April, struck at 4% and fixed at
  4.20% on the 15th, settles at its start for its discounted difference.
* **A repo**: 50,000 lent for a week at 4.30% ACT/360, repaid on the 24th.

Each one's cash is reconciled to the minor unit by hand. Building the schedule
refuses a fixing that is not supplied, one published only after the payment
that needs it, and a record that trades past its last payment.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from alphalab.backtesting import BacktestEngine, MarketDataset
from alphalab.backtesting.rates_payments import (
    PaymentTime,
    PublishedFixing,
    rates_payments,
)
from alphalab.backtesting.schedule import RunSchedule, ScheduleValidationError
from alphalab.common.arithmetic import accounting_context
from alphalab.common.order_terms import OrderTerms
from alphalab.conventions import BusinessDayConvention, DayCount, Tenor
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.rates import (
    FixedLeg,
    FloaterTerms,
    FloatingLeg,
    FraTerms,
    LegSchedule,
    RepoTerms,
    SwapTerms,
    fra_settlement,
    repo_repayment,
)
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    START_CASH,
    backtest_config,
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
)

D = Decimal
NY = ZoneInfo("America/New_York")
PAID = PaymentTime("America/New_York", time(17))
MODIFIED = BusinessDayConvention.MODIFIED_FOLLOWING
SIZE = D("50000")


@dataclass(frozen=True)
class Weekdays:
    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays


CALENDARS = {"USNY": Weekdays()}


def ny(day: date, hour: int) -> float:
    return datetime.combine(day, time(hour), tzinfo=NY).timestamp()


START, MATURITY = date(2025, 1, 17), date(2026, 1, 19)
SOFR = [
    (date(2025, 1, 15), "0.043"),
    (date(2025, 4, 15), "0.042"),
    (date(2025, 7, 15), "0.041"),
    (date(2025, 10, 15), "0.040"),
]
FIXINGS = [PublishedFixing("USD-SOFR-3M", day, D(rate), ny(day, 11)) for day, rate in SOFR]
PRICED = InstrumentEconomics(D("1"), SettlementModel.CASH_EQUITY, allows_negative_prices=True)
QUARTERLY = LegSchedule(Tenor.parse("3M"), MODIFIED, ("USNY",), False)

SWAP = InstrumentRecord(
    "USD-1Y-SWAP",
    AssetType.SWAP,
    "XOTC",
    "USD",
    economics=PRICED,
    last_trade_at=ny(MATURITY, 17),
    terms=SwapTerms(
        "USD",
        START,
        Tenor.parse("1Y"),
        FixedLeg(
            D("1"),
            D("0.04"),
            DayCount.THIRTY_360_US,
            LegSchedule(Tenor.parse("6M"), MODIFIED, ("USNY",), False),
        ),
        FloatingLeg(
            D("1"),
            "USD-SOFR-3M",
            D("0"),
            DayCount.ACT_360,
            QUARTERLY,
            2,
            "TEST",
        ),
    ),
)
FRA_TERMS = FraTerms(
    "USD",
    D("1"),
    D("0.04"),
    date(2025, 4, 17),
    date(2025, 7, 17),
    DayCount.ACT_360,
    "USD-SOFR-3M",
    2,
    ("USNY",),
    "TEST",
)
FRA = InstrumentRecord(
    "USD-3X6",
    AssetType.FRA,
    "XOTC",
    "USD",
    economics=PRICED,
    last_trade_at=ny(date(2025, 4, 17), 17),
    terms=FRA_TERMS,
)
REPO_TERMS = RepoTerms(
    "USD", "UST-2035", D("0.043"), START, date(2025, 1, 24), DayCount.ACT_360, D("0.02")
)
REPO = InstrumentRecord(
    "USD-REPO-1W",
    AssetType.REPO,
    "XOTC",
    "USD",
    economics=InstrumentEconomics(D("1"), SettlementModel.CASH_EQUITY),
    last_trade_at=ny(date(2025, 1, 24), 17),
    terms=REPO_TERMS,
)
FIRST, LAST = ny(START, 10), ny(date(2026, 1, 20), 10)


class Buyer(BaseStrategy):
    """Buys what its plan says at the first quote of each asset."""

    def __init__(self, plan: dict[str, Decimal]) -> None:
        self._plan = dict(plan)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        asset = event.quote.asset_id
        if event.quote.timestamp != FIRST or asset not in self._plan:
            return ()
        return (Intent("S", asset, self._plan.pop(asset), terms=OrderTerms(), timestamp=FIRST),)


def run(record: InstrumentRecord, price: str) -> Any:
    payments = rates_payments(record, CALENDARS, PAID, start=FIRST, end=LAST, fixings=FIXINGS)
    base = backtest_config("S")
    config = replace(base, pipeline=replace(base.pipeline, instruments=registry_of(record)))
    quotes = [
        quote(record.asset_id, at, D(price)) for at in (FIRST, ny(date(2025, 6, 2), 10), LAST)
    ]
    return BacktestEngine.run(
        config,
        MarketDataset.of("RATES", quotes),
        running_strategy_state("S", Buyer({record.asset_id: SIZE})),
        context_factory,
        schedule=RunSchedule.of(
            distributions=payments.distributions, settlements=payments.settlements
        ),
    )


def cents(amount: Decimal) -> Decimal:
    return amount.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)


def test_a_receiver_swaps_coupons_are_booked_netted_and_it_closes_at_its_last() -> None:
    result = run(SWAP, "0.0005")
    (fill,) = result.fills
    assert (fill.quantity, fill.price) == (SIZE, D("0.0005"))
    # Per unit, by hand: 30/360 counts 180 days to 17 July and 182 to 19 January;
    # ACT/360 counts 90, 91, 92 and 94 days.
    with accounting_context():
        floating = [
            D("0.043") * D(repr(90 / 360)),
            D("0.042") * D(repr(91 / 360)),
            D("0.041") * D(repr(92 / 360)),
            D("0.040") * D(repr(94 / 360)),
        ]
        fixed = [D("0.04") * D(repr(180 / 360)), D("0.04") * D(repr(182 / 360))]
        per_unit = [-floating[0], fixed[0] - floating[1], -floating[2], fixed[1] - floating[3]]
    payments = rates_payments(SWAP, CALENDARS, PAID, start=FIRST, end=LAST, fixings=FIXINGS)
    assert [d.per_unit for d in payments.distributions] == per_unit
    assert [d.at for d in payments.distributions] == [
        ny(date(2025, 4, 17), 17),
        ny(date(2025, 7, 17), 17),
        ny(date(2025, 10, 17), 17),
        ny(MATURITY, 17),
    ]
    booked = sum((cents(SIZE * amount) for amount in per_unit), D(0))
    expected = START_CASH - cents(SIZE * D("0.0005")) - fill.commission + booked
    assert result.state.portfolio.cash.balance("USD") == expected
    position = result.state.portfolio.positions.get(SWAP.asset_id)
    assert position is None or position.quantity == 0  # closed at nothing, at its last payment


def test_an_fra_settles_at_its_start_for_its_discounted_difference() -> None:
    result = run(FRA, "0")
    (fill,) = result.fills
    per_unit = fra_settlement(FRA_TERMS, D("0.042"))  # fixed on 15 April, two business days before
    expected = START_CASH - fill.commission + cents(SIZE * per_unit)
    assert result.state.portfolio.cash.balance("USD") == expected
    assert per_unit > 0  # fixed above its rate: the holder receives


def test_a_repo_lends_for_its_week_and_is_repaid_its_interest() -> None:
    result = run(REPO, "1")
    (fill,) = result.fills
    repaid = cents(SIZE * repo_repayment(REPO_TERMS))
    assert repaid == D("50041.81")  # 50,000 * (1 + 0.043 * 7 / 360)
    expected = START_CASH - SIZE - fill.commission + repaid
    assert result.state.portfolio.cash.balance("USD") == expected


def test_a_floater_quoted_per_hundred_pays_its_coupons_and_is_redeemed_at_par() -> None:
    # One unit is one dollar of face, quoted per hundred: a multiplier of 0.01. Its
    # redemption of one a unit is a final price of 100.
    terms = FloaterTerms(
        "USD",
        START,
        Tenor.parse("1Y"),
        FloatingLeg(D("1"), "USD-SOFR-3M", D("0.005"), DayCount.ACT_360, QUARTERLY, 2, "TEST"),
        D("1"),
    )
    note = InstrumentRecord(
        "FRN-2026",
        AssetType.BOND,
        "XOTC",
        "USD",
        economics=InstrumentEconomics(D("0.01"), SettlementModel.CASH_EQUITY),
        last_trade_at=ny(MATURITY, 17),
        terms=terms,
    )
    result = run(note, "99.5")
    (fill,) = result.fills
    payments = rates_payments(note, CALENDARS, PAID, start=FIRST, end=LAST, fixings=FIXINGS)
    (settlement,) = payments.settlements
    assert settlement.settlement.price == D("100")
    with accounting_context():
        coupons = [
            (D(rate) + D("0.005")) * D(repr(days / 360))
            for (_, rate), days in zip(SOFR, (90, 91, 92, 94), strict=True)
        ]
    assert [d.per_unit for d in payments.distributions] == coupons
    paid = START_CASH - cents(SIZE * D("99.5") * D("0.01")) - fill.commission
    expected = (
        paid + sum((cents(SIZE * c) for c in coupons), D(0)) + cents(SIZE * D("100") * D("0.01"))
    )
    assert result.state.portfolio.cash.balance("USD") == expected


def test_a_fixing_is_supplied_and_published_before_the_payment_that_needs_it() -> None:
    missing = [f for f in FIXINGS if f.fixed_on != date(2025, 7, 15)]
    with pytest.raises(ScheduleValidationError, match="fixing on 2025-07-15, and none is supplied"):
        rates_payments(SWAP, CALENDARS, PAID, start=FIRST, end=LAST, fixings=missing)
    late = [
        *missing,
        PublishedFixing("USD-SOFR-3M", date(2025, 7, 15), D("0.041"), ny(date(2025, 11, 3), 11)),
    ]
    with pytest.raises(ScheduleValidationError, match=r"published at .* after it"):
        rates_payments(SWAP, CALENDARS, PAID, start=FIRST, end=LAST, fixings=late)
    # A window over the first half-year needs only the fixings its payments read.
    early = rates_payments(
        SWAP, CALENDARS, PAID, start=FIRST, end=ny(date(2025, 7, 18), 10), fixings=FIXINGS[:2]
    )
    assert len(early.distributions) == 2 and early.settlements == ()


def test_a_record_that_trades_past_its_last_payment_is_refused() -> None:
    open_ended = replace(SWAP, last_trade_at=ny(date(2026, 6, 1), 17))
    with pytest.raises(ScheduleValidationError, match="trades until"):
        rates_payments(open_ended, CALENDARS, PAID, start=FIRST, end=LAST, fixings=FIXINGS)
    bond = InstrumentRecord(
        "UST-2035",
        AssetType.BOND,
        "XNYS",
        "USD",
        economics=InstrumentEconomics(D("1"), SettlementModel.CASH_EQUITY),
    )
    with pytest.raises(ScheduleValidationError, match="declares no rates terms"):
        rates_payments(bond, CALENDARS, PAID, start=FIRST, end=LAST)
