"""Business days, tenors and FX value dates by declared rules (v4.3, plan N21, ADR-0067).

Every expected date is worked out by hand on calendars stated here: weekends,
and the holidays each case names.

* **Conventions** (ISDA 2006, section 4.12) at a weekend and at a month's end,
  where modified following turns back.
* **Tenors**: a month from the 31st clamps to the shorter month; from the last
  business day of February, the end-of-month rule reaches the last business day
  of March, and without it the 28th, adjusted.
* **Value dates.** A USD pair counts its lag in the other currency's calendar,
  so Thanksgiving the day after the trade does not delay spot. Its value date
  must be open in both, so the Fourth of July moves it. A cross counted in both
  its currencies and settling on a US business day moves too. A forward is its
  tenor from spot, under the end-of-month rule from a month's last business day.
* **Refusals**: no calendar is assumed open and no convention defaulted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, time

import pytest

from alphalab.conventions import ConventionInputError
from alphalab.conventions.business_days import (
    BusinessDayConvention,
    Tenor,
    TenorUnit,
    ValueDateRule,
    add_business_days,
    add_tenor,
    adjust,
    forward_value_date,
    is_business_day,
    spot_value_date,
)
from alphalab.data.calendar import MarketCalendar, SessionWindow

FOLLOWING = BusinessDayConvention.FOLLOWING
MODIFIED = BusinessDayConvention.MODIFIED_FOLLOWING
PRECEDING = BusinessDayConvention.PRECEDING


@dataclass(frozen=True)
class Weekdays:
    """Open Monday to Friday, except the holidays it names."""

    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays


PLAIN = Weekdays()
US = Weekdays(frozenset({date(2025, 7, 4), date(2025, 11, 27), date(2025, 12, 25)}))
EUR = Weekdays(frozenset({date(2025, 12, 25), date(2025, 12, 26)}))
JPY = Weekdays(frozenset({date(2025, 11, 24)}))


# --------------------------------------------------------------------------- #
# Conventions
# --------------------------------------------------------------------------- #


def test_each_convention_moves_a_weekend_its_own_way() -> None:
    saturday = date(2025, 3, 1)
    assert adjust(saturday, FOLLOWING, [PLAIN]) == date(2025, 3, 3)
    assert adjust(saturday, PRECEDING, [PLAIN]) == date(2025, 2, 28)
    # The first following business day is in March, the same month: kept.
    assert adjust(saturday, MODIFIED, [PLAIN]) == date(2025, 3, 3)
    assert adjust(saturday, BusinessDayConvention.UNADJUSTED, [PLAIN]) == saturday
    # A business day is never moved.
    assert adjust(date(2025, 3, 3), PRECEDING, [PLAIN]) == date(2025, 3, 3)


def test_modified_following_turns_back_at_a_months_end() -> None:
    # Saturday 31 May 2025: the following business day is 2 June, in the next
    # month, so modified following takes the preceding one, Friday 30 May.
    assert adjust(date(2025, 5, 31), FOLLOWING, [PLAIN]) == date(2025, 6, 2)
    assert adjust(date(2025, 5, 31), MODIFIED, [PLAIN]) == date(2025, 5, 30)


def test_every_calendar_named_must_be_open() -> None:
    # Friday 4 July 2025 is open in Europe and shut in the US: not a joint business day.
    fourth = date(2025, 7, 4)
    assert is_business_day(fourth, [EUR]) and not is_business_day(fourth, [EUR, US])
    assert adjust(fourth, FOLLOWING, [EUR, US]) == date(2025, 7, 7)


def test_business_days_are_counted_past_weekends_and_holidays() -> None:
    assert add_business_days(date(2025, 1, 3), 2, [PLAIN]) == date(2025, 1, 7)
    holiday = Weekdays(frozenset({date(2025, 1, 6)}))
    assert add_business_days(date(2025, 1, 3), 2, [holiday]) == date(2025, 1, 8)
    assert add_business_days(date(2025, 1, 4), 0, [PLAIN]) == date(2025, 1, 4)


# --------------------------------------------------------------------------- #
# Tenors
# --------------------------------------------------------------------------- #


def test_a_month_from_the_31st_clamps_to_the_shorter_month() -> None:
    assert add_tenor(
        date(2025, 1, 31), Tenor(1, TenorUnit.MONTHS), MODIFIED, [PLAIN], end_of_month=False
    ) == date(2025, 2, 28)


def test_the_end_of_month_rule_is_stated_not_assumed() -> None:
    # Friday 28 February 2025 is February's last business day.
    start = date(2025, 2, 28)
    month = Tenor(1, TenorUnit.MONTHS)
    assert add_tenor(start, month, MODIFIED, [PLAIN], end_of_month=True) == date(2025, 3, 31)
    assert add_tenor(start, month, MODIFIED, [PLAIN], end_of_month=False) == date(2025, 3, 28)
    # From a day that is not its month's last business day the rule changes nothing.
    assert add_tenor(date(2025, 2, 27), month, MODIFIED, [PLAIN], end_of_month=True) == date(
        2025, 3, 27
    )


def test_years_weeks_and_days_step_as_their_units_say() -> None:
    assert add_tenor(
        date(2025, 1, 15), Tenor(1, TenorUnit.YEARS), FOLLOWING, [PLAIN], end_of_month=False
    ) == date(2026, 1, 15)
    assert add_tenor(
        date(2025, 1, 1), Tenor(2, TenorUnit.WEEKS), FOLLOWING, [PLAIN], end_of_month=False
    ) == date(2025, 1, 15)
    # Ten days from Wednesday 1 January is Saturday the 11th: following, Monday.
    assert add_tenor(
        date(2025, 1, 1), Tenor(10, TenorUnit.DAYS), FOLLOWING, [PLAIN], end_of_month=False
    ) == date(2025, 1, 13)
    assert Tenor.parse("3M") == Tenor(3, TenorUnit.MONTHS) and Tenor.parse("1y").label == "1Y"


# --------------------------------------------------------------------------- #
# Value dates
# --------------------------------------------------------------------------- #

#: EUR/USD: the lag counted in Europe's calendar, the value date open in both.
EURUSD = ValueDateRule(2, (EUR,), (EUR, US))
#: EUR/JPY: counted in both currencies, settling on a US business day too.
EURJPY = ValueDateRule(2, (EUR, JPY), (EUR, JPY, US))


def test_a_us_holiday_on_the_value_date_moves_it() -> None:
    # Struck Wednesday 2 July 2025: two European business days reach Friday 4 July,
    # a US holiday, so spot rolls to Monday 7 July.
    assert spot_value_date(date(2025, 7, 2), EURUSD) == date(2025, 7, 7)


def test_a_us_holiday_before_spot_does_not_delay_a_usd_pair() -> None:
    # Struck Wednesday 26 November 2025: Thanksgiving, the 27th, is a European
    # business day and counts; spot is Friday the 28th, open in both.
    assert spot_value_date(date(2025, 11, 26), EURUSD) == date(2025, 11, 28)
    # Counted in both calendars instead, Thanksgiving would not count.
    both = ValueDateRule(2, (EUR, US), (EUR, US))
    assert spot_value_date(date(2025, 11, 26), both) == date(2025, 12, 1)


def test_a_cross_settles_on_a_us_business_day_too() -> None:
    assert spot_value_date(date(2025, 7, 2), EURJPY) == date(2025, 7, 7)
    # Struck Friday 21 November: Monday the 24th is a Japanese holiday and does not
    # count, so two days in both reach Wednesday the 26th.
    assert spot_value_date(date(2025, 11, 21), EURJPY) == date(2025, 11, 26)


def test_a_one_day_lag_and_a_same_day_deal() -> None:
    usdcad = ValueDateRule(1, (PLAIN,), (PLAIN,))
    assert spot_value_date(date(2025, 1, 3), usdcad) == date(2025, 1, 6)
    today = ValueDateRule(0, (PLAIN,), (PLAIN,))
    assert spot_value_date(date(2025, 1, 3), today) == date(2025, 1, 3)


def test_a_forward_is_its_tenor_from_spot_under_the_end_of_month_rule() -> None:
    month, two = Tenor(1, TenorUnit.MONTHS), Tenor(2, TenorUnit.MONTHS)
    # Struck Tuesday 28 January 2025: spot is Thursday the 30th, and a month later
    # the 30th of February clamps to the 28th, a Friday.
    assert forward_value_date(
        date(2025, 1, 28), EURUSD, month, MODIFIED, end_of_month=True
    ) == date(2025, 2, 28)
    # Struck Wednesday 29 January: spot is Friday the 31st, January's last
    # business day, so the end-of-month rule holds each month's last.
    assert forward_value_date(
        date(2025, 1, 29), EURUSD, month, MODIFIED, end_of_month=True
    ) == date(2025, 2, 28)
    assert forward_value_date(date(2025, 1, 29), EURUSD, two, MODIFIED, end_of_month=True) == date(
        2025, 3, 31
    )


def test_a_market_calendar_is_a_calendar_here() -> None:
    weekdays = dict.fromkeys(range(5), (SessionWindow(time(9, 0), time(17, 0)),))
    calendar = MarketCalendar(
        "XTEST", "Europe/London", weekdays, holidays=frozenset({date(2025, 12, 25)})
    )
    assert adjust(date(2025, 12, 25), FOLLOWING, [calendar]) == date(2025, 12, 26)


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: Tenor(0, TenorUnit.MONTHS), "positive integer"),
        (lambda: Tenor(1, "M"), "must be a TenorUnit"),  # type: ignore[arg-type]
        (lambda: Tenor.parse("3X"), "not D, W, M or Y"),
        (lambda: Tenor.parse("M"), "is not a tenor"),
        (lambda: ValueDateRule(-1, (PLAIN,), (PLAIN,)), "non-negative"),
        (lambda: ValueDateRule(2, (), (PLAIN,)), "at least one calendar"),
        (lambda: ValueDateRule(2, (PLAIN,), (object(),)), "is not a calendar"),  # type: ignore[arg-type]
        (lambda: adjust(date(2025, 1, 4), "following", [PLAIN]), "not a BusinessDayConvention"),  # type: ignore[arg-type]
        (lambda: adjust(date(2025, 1, 4), FOLLOWING, []), "at least one calendar"),
        (lambda: add_business_days(date(2025, 1, 4), -1, [PLAIN]), "non-negative"),
        (
            lambda: add_tenor(
                date(2025, 1, 4),
                Tenor(1, TenorUnit.MONTHS),
                FOLLOWING,
                [PLAIN],
                end_of_month=None,  # type: ignore[arg-type]
            ),
            "True or False",
        ),
    ],
    ids=[
        "count",
        "unit",
        "parse-unit",
        "parse-count",
        "lag",
        "counted",
        "calendar",
        "convention",
        "no-calendar",
        "business-days",
        "end-of-month",
    ],
)
def test_nothing_is_assumed(build: Callable[[], object], reason: str) -> None:
    with pytest.raises(ConventionInputError, match=reason):
        build()
