"""A leg's schedule and a quote's cash flows, by their declared conventions (v4.3, ADR-0065).

Every date and amount is worked out by hand:

* **A regular schedule** counts each date from its start, so a 31 January
  start under the end-of-month rule lands on each month's last business day,
  and modified following turns a month-end Saturday back.
* **A stub is refused**, never placed, and a step in days is no whole month.
* **A bond's coupons** step back from maturity. Under ACT/ACT (ICMA) each
  regular coupon is exactly the coupon over the frequency. Under ACT/365F a
  coupon is its period's days over 365. A zero-coupon bond's only flow is its
  redemption.
* **A quote states its conventions**: a basis needing an instrument's terms is
  refused where the quote has none to give, and a frequency must divide a
  year into whole months.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest

from alphalab.conventions import BusinessDayConvention, DayCount, Tenor
from alphalab.rates import (
    BondQuote,
    DepositQuote,
    FutureQuote,
    RatesInputError,
    SwapQuote,
    regular_schedule,
)

MODIFIED = BusinessDayConvention.MODIFIED_FOLLOWING


@dataclass(frozen=True)
class Weekdays:
    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays


PLAIN = (Weekdays(),)


def test_a_regular_schedule_counts_each_date_from_its_start() -> None:
    # 31 January 2025 is a Friday, its month's last business day.
    start = date(2025, 1, 31)
    held = regular_schedule(
        start, Tenor.parse("6M"), Tenor.parse("1M"), MODIFIED, PLAIN, end_of_month=True
    )
    assert held == (
        date(2025, 1, 31),
        date(2025, 2, 28),
        date(2025, 3, 31),
        date(2025, 4, 30),
        date(2025, 5, 30),  # 31 May is a Saturday: the last business day is the 30th
        date(2025, 6, 30),
        date(2025, 7, 31),
    )
    # Without the rule each date is the 31st clamped to its month, then adjusted.
    loose = regular_schedule(
        start, Tenor.parse("6M"), Tenor.parse("1M"), MODIFIED, PLAIN, end_of_month=False
    )
    assert loose[1] == date(2025, 2, 28) and loose[4] == date(2025, 5, 30)


def test_a_stub_and_a_step_in_days_are_refused() -> None:
    with pytest.raises(RatesInputError, match="stub"):
        regular_schedule(
            date(2025, 1, 15),
            Tenor.parse("18M"),
            Tenor.parse("1Y"),
            MODIFIED,
            PLAIN,
            end_of_month=False,
        )
    with pytest.raises(RatesInputError, match="months or years"):
        regular_schedule(
            date(2025, 1, 15),
            Tenor.parse("1Y"),
            Tenor.parse("28D"),
            MODIFIED,
            PLAIN,
            end_of_month=False,
        )
    with pytest.raises(RatesInputError, match="at least one calendar"):
        regular_schedule(
            date(2025, 1, 15),
            Tenor.parse("1Y"),
            Tenor.parse("6M"),
            MODIFIED,
            (),
            end_of_month=False,
        )


def test_a_bonds_coupons_step_back_from_maturity_by_its_basis() -> None:
    # A seasoned semi-annual 5% bond maturing 15 November 2027, settled 10 March 2025.
    icma = BondQuote(date(2025, 3, 10), date(2027, 11, 15), 0.05, 2, DayCount.ACT_ACT_ICMA, 101.0)
    assert [day for day, _ in icma.flows] == [
        date(2025, 5, 15),
        date(2025, 11, 15),
        date(2026, 5, 15),
        date(2026, 11, 15),
        date(2027, 5, 15),
        date(2027, 11, 15),
    ]
    assert [amount for _, amount in icma.flows] == [2.5] * 5 + [102.5]
    # Under ACT/365F a coupon is its period's days over 365: 181 days to 15 May 2025.
    actual = BondQuote(
        date(2025, 3, 10), date(2027, 11, 15), 0.05, 2, DayCount.ACT_365_FIXED, 101.0
    )
    first, amount = actual.flows[0]
    assert first == date(2025, 5, 15)
    assert amount == pytest.approx(100 * 0.05 * 181 / 365, rel=1e-15)
    zero = BondQuote(date(2025, 3, 10), date(2027, 11, 15), 0.0, 2, DayCount.ACT_ACT_ICMA, 88.0)
    assert zero.flows == ((date(2027, 11, 15), 100.0),)


def test_a_month_end_maturity_keeps_its_coupons_at_month_ends() -> None:
    bond = BondQuote(date(2025, 1, 2), date(2026, 8, 31), 0.04, 2, DayCount.THIRTY_360_US, 100.0)
    assert [day for day, _ in bond.flows] == [
        date(2025, 2, 28),
        date(2025, 8, 31),
        date(2026, 2, 28),
        date(2026, 8, 31),
    ]


def test_a_quote_states_its_conventions() -> None:
    with pytest.raises(RatesInputError, match="needs an instrument's"):
        DepositQuote(date(2025, 1, 15), date(2025, 4, 15), 0.04, DayCount.ACT_ACT_ICMA)
    with pytest.raises(RatesInputError, match="1, 2, 3, 4, 6 or 12"):
        BondQuote(date(2025, 1, 15), date(2027, 1, 15), 0.04, 5, DayCount.THIRTY_360_US, 100.0)
    with pytest.raises(RatesInputError, match="later date"):
        FutureQuote(date(2025, 6, 18), date(2025, 6, 18), 96.0, DayCount.ACT_360, 0.0)
    with pytest.raises(RatesInputError, match="finite number"):
        FutureQuote(date(2025, 6, 18), date(2025, 9, 17), 96.0, DayCount.ACT_360, float("nan"))
    with pytest.raises(RatesInputError, match="needs holidays, weekend"):
        SwapQuote(
            date(2025, 1, 15),
            Tenor.parse("2Y"),
            0.04,
            Tenor.parse("1Y"),
            DayCount.BUSINESS_252,
            Tenor.parse("6M"),
            MODIFIED,
            PLAIN,
            end_of_month=False,
        )


def test_a_swaps_fixed_leg_reads_each_periods_own_terms() -> None:
    # Under ACT/ACT (ICMA) each annual period accrues exactly one year, whatever its days.
    quote = SwapQuote(
        date(2025, 1, 15),
        Tenor.parse("3Y"),
        0.04,
        Tenor.parse("1Y"),
        DayCount.ACT_ACT_ICMA,
        Tenor.parse("3M"),
        MODIFIED,
        PLAIN,
        end_of_month=False,
    )
    assert quote.fixed_accruals == (1.0, 1.0, 1.0)
    assert len(quote.float_dates) == 13 and quote.pillar == date(
        2028, 1, 17
    )  # the 15th is a Saturday
