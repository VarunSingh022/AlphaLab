"""Market calendars: sessions in the venue's own time, for any venue.

The brief's requirement is that no single market is privileged. These tests
construct India, the United States, Japan, a 24-hour derivatives venue and a
crypto exchange from the same abstraction, and assert that each behaves the way
that market actually behaves -- lunch breaks, half days, overnight sessions,
holidays and daylight saving included.

AlphaLab ships none of these calendars. Every one below is declared by the
test, which is exactly how an application declares its own.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from alphalab.data import CONTINUOUS_SESSION, DataValidationError, MarketCalendar, SessionWindow

WEEKDAYS = range(5)


def _at(year: int, month: int, day: int, hour: int, minute: int, zone: str) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(zone)).timestamp()


NSE = MarketCalendar(
    calendar_id="XNSE",
    timezone_name="Asia/Kolkata",
    weekly_sessions={day: (SessionWindow(time(9, 15), time(15, 30)),) for day in WEEKDAYS},
    holidays=frozenset({date(2025, 1, 26)}),
)

NYSE = MarketCalendar(
    calendar_id="XNYS",
    timezone_name="America/New_York",
    weekly_sessions={day: (SessionWindow(time(9, 30), time(16, 0)),) for day in WEEKDAYS},
    special_sessions={date(2025, 11, 28): (SessionWindow(time(9, 30), time(13, 0)),)},
)

TSE = MarketCalendar(
    calendar_id="XJPX",
    timezone_name="Asia/Tokyo",
    weekly_sessions={
        day: (SessionWindow(time(9, 0), time(11, 30)), SessionWindow(time(12, 30), time(15, 0)))
        for day in WEEKDAYS
    },
)

CME = MarketCalendar(
    calendar_id="XCME",
    timezone_name="America/Chicago",
    weekly_sessions={day: (SessionWindow(time(17, 0), time(16, 0)),) for day in range(7)},
)

BINANCE = MarketCalendar.continuous("BINANCE", "UTC")


# --------------------------------------------------------------------------- #
# Sessions in local time
# --------------------------------------------------------------------------- #


def test_a_venue_is_open_during_its_own_session_and_closed_outside_it() -> None:
    assert NSE.is_open(_at(2025, 1, 6, 10, 0, "Asia/Kolkata")) is True
    assert NSE.is_open(_at(2025, 1, 6, 16, 0, "Asia/Kolkata")) is False
    assert NSE.is_open(_at(2025, 1, 6, 9, 0, "Asia/Kolkata")) is False


def test_one_instant_is_open_in_one_market_and_shut_in_another() -> None:
    """The reason a calendar carries a zone: 14:30 UTC is mid-session in New
    York and the middle of the night in Mumbai."""

    instant = _at(2025, 1, 6, 14, 30, "UTC")

    assert NYSE.is_open(instant) is True
    assert NSE.is_open(instant) is False


def test_a_declared_holiday_is_not_a_trading_day() -> None:
    assert NSE.is_trading_day(date(2025, 1, 24)) is True
    assert NSE.is_trading_day(date(2025, 1, 26)) is False, "Republic Day, as declared"
    assert NSE.windows_on(date(2025, 1, 26)) == ()


def test_a_weekend_is_not_a_trading_day() -> None:
    assert NSE.is_trading_day(date(2025, 1, 4)) is False


# --------------------------------------------------------------------------- #
# Shapes that are not one continuous block
# --------------------------------------------------------------------------- #


def test_a_lunch_break_is_genuinely_closed() -> None:
    """Tokyo, Hong Kong and Singapore all break for lunch; a calendar that
    could not say so would report a halt as ordinary trading."""

    assert TSE.is_open(_at(2025, 1, 6, 10, 0, "Asia/Tokyo")) is True
    assert TSE.is_open(_at(2025, 1, 6, 12, 0, "Asia/Tokyo")) is False
    assert TSE.is_open(_at(2025, 1, 6, 13, 0, "Asia/Tokyo")) is True


def test_the_day_bounds_span_the_break_without_claiming_it_was_open() -> None:
    bounds = TSE.session_bounds(date(2025, 1, 6))

    assert bounds is not None
    assert bounds[0] == _at(2025, 1, 6, 9, 0, "Asia/Tokyo")
    assert bounds[1] == _at(2025, 1, 6, 15, 0, "Asia/Tokyo")
    assert TSE.is_open(_at(2025, 1, 6, 12, 0, "Asia/Tokyo")) is False


def test_a_half_day_replaces_the_usual_session() -> None:
    assert NYSE.is_open(_at(2025, 11, 28, 12, 0, "America/New_York")) is True
    assert NYSE.is_open(_at(2025, 11, 28, 14, 0, "America/New_York")) is False
    assert NYSE.is_open(_at(2025, 11, 21, 14, 0, "America/New_York")) is True


def test_an_overnight_session_stays_open_past_midnight() -> None:
    """CME trades 17:00 to 16:00 the next day. A calendar that only looked at
    the current local date would report 02:00 as closed."""

    assert CME.is_open(_at(2025, 1, 7, 2, 0, "America/Chicago")) is True
    assert CME.is_open(_at(2025, 1, 7, 16, 30, "America/Chicago")) is False, "the daily break"


def test_an_overnight_trade_belongs_to_the_session_that_opened_the_day_before() -> None:
    """What the venue's own daily statement says, and not what the wall clock
    reads -- which is the distinction a daily bar depends on."""

    assert CME.trading_day_of(_at(2025, 1, 7, 2, 0, "America/Chicago")) == date(2025, 1, 6)
    assert CME.trading_day_of(_at(2025, 1, 7, 18, 0, "America/Chicago")) == date(2025, 1, 7)
    assert CME.trading_day_of(_at(2025, 1, 7, 16, 30, "America/Chicago")) is None


# --------------------------------------------------------------------------- #
# Markets that never close
# --------------------------------------------------------------------------- #


def test_a_continuous_market_is_open_at_every_instant_including_sundays() -> None:
    assert BINANCE.is_continuous is True
    assert BINANCE.is_open(_at(2025, 1, 5, 3, 0, "UTC")) is True
    assert BINANCE.is_trading_day(date(2025, 1, 5)) is True
    assert BINANCE.windows_on(date(2025, 1, 5)) == (CONTINUOUS_SESSION,)


def test_a_continuous_market_still_has_a_zone_because_a_daily_bar_needs_one() -> None:
    tokyo_crypto = MarketCalendar.continuous("VENUE", "Asia/Tokyo")

    instant = _at(2025, 1, 2, 22, 0, "UTC")
    assert tokyo_crypto.local_datetime(instant).date() == date(2025, 1, 3)
    assert BINANCE.local_datetime(instant).date() == date(2025, 1, 2)


def test_an_exchange_calendar_is_not_continuous() -> None:
    assert NSE.is_continuous is False


# --------------------------------------------------------------------------- #
# Daylight saving
# --------------------------------------------------------------------------- #


def test_a_session_keeps_its_local_hours_across_a_daylight_saving_change() -> None:
    """09:30 in New York is 14:30 UTC in January and 13:30 UTC in March. A
    calendar storing UTC offsets would have the session an hour wrong for half
    the year."""

    january = NYSE.session_bounds(date(2025, 1, 6))
    march = NYSE.session_bounds(date(2025, 3, 10))

    assert january is not None and march is not None
    assert datetime.fromtimestamp(january[0], tz=ZoneInfo("UTC")).hour == 14
    assert datetime.fromtimestamp(march[0], tz=ZoneInfo("UTC")).hour == 13
    assert NYSE.is_open(_at(2025, 3, 10, 10, 0, "America/New_York")) is True


# --------------------------------------------------------------------------- #
# Ranges and refusals
# --------------------------------------------------------------------------- #


def test_sessions_between_counts_only_trading_days() -> None:
    sessions = NSE.sessions_between(date(2025, 1, 1), date(2025, 1, 31))

    assert len(sessions) == 23, "23 weekdays in January 2025, less Republic Day"
    assert date(2025, 1, 26) not in sessions
    assert date(2025, 1, 4) not in sessions


def test_a_backwards_range_is_refused() -> None:
    with pytest.raises(DataValidationError) as error:
        NSE.sessions_between(date(2025, 1, 31), date(2025, 1, 1))

    assert "precedes start" in str(error.value)


def test_an_unnamed_calendar_is_refused() -> None:
    with pytest.raises(DataValidationError):
        MarketCalendar("  ", "UTC", {})


def test_an_unknown_zone_is_refused_with_a_usable_message() -> None:
    with pytest.raises(DataValidationError) as error:
        MarketCalendar("X", "Mars/Olympus", {})

    assert "tzdata" in str(error.value), "names the likely cause on a host with no tz database"


def test_an_impossible_weekday_is_refused() -> None:
    with pytest.raises(DataValidationError) as error:
        MarketCalendar("X", "UTC", {7: (SessionWindow(time(9, 0), time(17, 0)),)})

    assert "0 (Monday)" in str(error.value)
