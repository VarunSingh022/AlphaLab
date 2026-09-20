"""Session semantics across timezones, daylight saving and overnight windows.

``tests/unit/data/test_calendar.py`` covers what a calendar *is*. This covers
what v3.4 added to it -- the trading-day arithmetic a settlement rule and a roll
rule count over -- and the cases a worldwide calendar has to be right about and
a single-market one never exercises.
"""

from datetime import UTC, date, datetime, time

import pytest

from alphalab.data.calendar import (
    CONTINUOUS_SESSION,
    MAX_SESSION_SEARCH_DAYS,
    MarketCalendar,
    SessionWindow,
)
from alphalab.data.exceptions import DataValidationError


def _at(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()


_NSE = MarketCalendar(
    calendar_id="XNSE",
    timezone_name="Asia/Kolkata",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 15), time(15, 30)),)),
)

_TSE = MarketCalendar(
    calendar_id="XTKS",
    timezone_name="Asia/Tokyo",
    weekly_sessions=dict.fromkeys(
        range(5),
        (SessionWindow(time(9, 0), time(11, 30)), SessionWindow(time(12, 30), time(15, 0))),
    ),
)

_CME = MarketCalendar(
    calendar_id="XCME",
    timezone_name="America/Chicago",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(17, 0), time(16, 0)),)),
)

_CRYPTO = MarketCalendar.continuous("BINANCE", "UTC")


# --------------------------------------------------------------------------- #
# Trading-day arithmetic
# --------------------------------------------------------------------------- #


def test_adding_zero_trading_days_is_the_day_itself_even_on_a_holiday() -> None:
    """T+0 settles on the trade date, whatever the calendar says about it."""

    closed = MarketCalendar(
        calendar_id="X",
        timezone_name="UTC",
        weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9), time(17)),)),
        holidays=frozenset({date(2026, 3, 16)}),
    )
    assert closed.add_trading_days(date(2026, 3, 16), 0) == date(2026, 3, 16)


def test_adding_a_trading_day_skips_the_weekend() -> None:
    friday = date(2026, 3, 13)
    assert friday.weekday() == 4
    assert _NSE.add_trading_days(friday, 1) == date(2026, 3, 16)


def test_a_negative_count_walks_backwards() -> None:
    monday = date(2026, 3, 16)
    assert _NSE.add_trading_days(monday, -1) == date(2026, 3, 13)
    assert _NSE.add_trading_days(monday, -3) == date(2026, 3, 11)


def test_the_day_itself_is_never_counted() -> None:
    """The convention every T+n settlement rule states."""

    monday = date(2026, 3, 16)
    assert _NSE.is_trading_day(monday)
    assert _NSE.add_trading_days(monday, 1) == date(2026, 3, 17)


def test_a_continuous_calendar_counts_every_day() -> None:
    assert _CRYPTO.add_trading_days(date(2026, 3, 13), 1) == date(2026, 3, 14)


def test_a_calendar_with_no_trading_day_refuses_rather_than_looping() -> None:
    never = MarketCalendar(calendar_id="SHUT", timezone_name="UTC", weekly_sessions={})
    with pytest.raises(DataValidationError, match=str(MAX_SESSION_SEARCH_DAYS)):
        never.add_trading_days(date(2026, 3, 13), 1)


# --------------------------------------------------------------------------- #
# Opens and closes
# --------------------------------------------------------------------------- #


def test_next_open_returns_the_instant_itself_while_the_market_trades() -> None:
    """Answering with the following session would skip the one in progress."""

    during = _at(2026, 3, 16, 5)  # 10:30 in Kolkata
    assert _NSE.is_open(during)
    assert _NSE.next_open(during) == during


def test_next_open_crosses_a_weekend() -> None:
    friday_evening = _at(2026, 3, 13, 12)  # 17:30 Kolkata, after the close
    opened = _NSE.next_open(friday_evening)
    assert opened is not None
    assert _NSE.local_datetime(opened).date() == date(2026, 3, 16)
    assert _NSE.local_datetime(opened).time() == time(9, 15)


def test_next_close_is_none_when_the_market_is_shut() -> None:
    """A different question from 'when does it next close'."""

    saturday = _at(2026, 3, 14, 5)
    assert not _NSE.is_open(saturday)
    assert _NSE.next_close(saturday) is None


def test_next_close_ends_the_session_in_progress() -> None:
    during = _at(2026, 3, 16, 5)
    closed = _NSE.next_close(during)
    assert closed is not None
    assert _NSE.local_datetime(closed).time() == time(15, 30)


def test_a_lunch_break_is_two_windows_and_one_envelope() -> None:
    """``session_windows_on`` can say the market was shut at noon; the envelope cannot."""

    day = date(2026, 3, 16)
    windows = _TSE.session_windows_on(day)
    assert len(windows) == 2
    bounds = _TSE.session_bounds(day)
    assert bounds is not None
    assert bounds == (windows[0][0], windows[-1][1])

    noon_tokyo = _at(2026, 3, 16, 3)  # 12:00 in Tokyo, inside the break
    assert bounds[0] < noon_tokyo < bounds[1]
    assert not _TSE.is_open(noon_tokyo)


# --------------------------------------------------------------------------- #
# Timezones, overnight sessions and the UTC date boundary
# --------------------------------------------------------------------------- #


def test_one_instant_belongs_to_different_local_days_in_different_venues() -> None:
    """The example the module docstring opens with, asserted."""

    instant = datetime(2025, 1, 2, 21, 30, tzinfo=UTC).timestamp()
    assert _NSE.local_datetime(instant).date() == date(2025, 1, 3)
    assert _TSE.local_datetime(instant).date() == date(2025, 1, 3)
    assert _CME.local_datetime(instant).date() == date(2025, 1, 2)


def test_an_overnight_session_belongs_to_the_day_it_opened_on() -> None:
    """A CME trade at 02:00 belongs to the session that started the evening before."""

    two_am_chicago = _at(2026, 3, 17, 7)  # 02:00 America/Chicago
    local = _CME.local_datetime(two_am_chicago)
    assert local.date() == date(2026, 3, 17)
    assert _CME.is_open(two_am_chicago)
    assert _CME.trading_day_of(two_am_chicago) == date(2026, 3, 16)


def test_a_continuous_calendar_is_open_at_every_instant_including_sundays() -> None:
    sunday = _at(2026, 3, 15, 3)
    assert _CRYPTO.is_open(sunday)
    assert _CRYPTO.trading_day_of(sunday) == date(2026, 3, 15)
    assert _CRYPTO.is_continuous
    assert _CRYPTO.weekly_sessions[6] == (CONTINUOUS_SESSION,)


def test_a_continuous_calendars_zone_still_decides_the_local_day() -> None:
    """Which is why ``continuous()`` requires a zone rather than assuming UTC."""

    tokyo_continuous = MarketCalendar.continuous("VENUE", "Asia/Tokyo")
    instant = datetime(2025, 1, 2, 21, 30, tzinfo=UTC).timestamp()
    assert _CRYPTO.trading_day_of(instant) == date(2025, 1, 2)
    assert tokyo_continuous.trading_day_of(instant) == date(2025, 1, 3)


# --------------------------------------------------------------------------- #
# Daylight saving
# --------------------------------------------------------------------------- #


def test_a_session_keeps_its_local_wall_clock_across_a_spring_forward() -> None:
    """US clocks move on 2026-03-08. The open stays 09:30 local and moves in UTC."""

    nyse = MarketCalendar(
        calendar_id="XNYS",
        timezone_name="America/New_York",
        weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 30), time(16, 0)),)),
    )
    before = nyse.session_bounds(date(2026, 3, 6))
    after = nyse.session_bounds(date(2026, 3, 9))
    assert before is not None and after is not None

    assert nyse.local_datetime(before[0]).time() == time(9, 30)
    assert nyse.local_datetime(after[0]).time() == time(9, 30)
    # 14:30 UTC before the change, 13:30 after: the same local session, an hour
    # earlier in absolute terms.
    assert datetime.fromtimestamp(before[0], tz=UTC).hour == 14
    assert datetime.fromtimestamp(after[0], tz=UTC).hour == 13


def test_a_session_spanning_a_spring_forward_is_an_hour_shorter() -> None:
    """The absolute duration changes even though the wall clock does not."""

    nyse = MarketCalendar(
        calendar_id="XNYS",
        timezone_name="America/New_York",
        weekly_sessions=dict.fromkeys(range(7), (SessionWindow(time(0, 30), time(6, 0)),)),
    )
    normal = nyse.session_bounds(date(2026, 3, 7))
    shortened = nyse.session_bounds(date(2026, 3, 8))
    assert normal is not None and shortened is not None
    assert normal[1] - normal[0] == 5.5 * 3600
    assert shortened[1] - shortened[0] == 4.5 * 3600


def test_an_autumn_fall_back_lengthens_the_session_by_an_hour() -> None:
    nyse = MarketCalendar(
        calendar_id="XNYS",
        timezone_name="America/New_York",
        weekly_sessions=dict.fromkeys(range(7), (SessionWindow(time(0, 30), time(6, 0)),)),
    )
    lengthened = nyse.session_bounds(date(2026, 11, 1))
    assert lengthened is not None
    assert lengthened[1] - lengthened[0] == 6.5 * 3600


def test_trading_day_arithmetic_is_unaffected_by_daylight_saving() -> None:
    """Days are local dates, so a clock change neither adds nor drops one."""

    nyse = MarketCalendar(
        calendar_id="XNYS",
        timezone_name="America/New_York",
        weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 30), time(16, 0)),)),
    )
    assert nyse.add_trading_days(date(2026, 3, 6), 1) == date(2026, 3, 9)
    assert len(nyse.sessions_between(date(2026, 3, 2), date(2026, 3, 13))) == 10


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_every_reading_is_deterministic() -> None:
    instant = _at(2026, 3, 16, 5)
    for calendar in (_NSE, _TSE, _CME, _CRYPTO):
        assert calendar.is_open(instant) == calendar.is_open(instant)
        assert calendar.next_open(instant) == calendar.next_open(instant)
        assert calendar.session_windows_on(date(2026, 3, 16)) == calendar.session_windows_on(
            date(2026, 3, 16)
        )
