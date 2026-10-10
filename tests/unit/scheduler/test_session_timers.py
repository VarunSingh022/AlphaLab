"""Session timers fire at a market's trading-day boundaries, from its calendar (SCF-003).

Until v3.12 ``SESSION_OPEN`` and ``SESSION_CLOSE`` were refused at registration,
because nothing could say when a session opened. They now follow a
:class:`~alphalab.data.calendar.MarketCalendar`: once per trading day each way,
in the venue's own zone, skipping weekends and holidays, across a lunch break
and across midnight.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from alphalab.data import MarketCalendar, SessionWindow
from alphalab.scheduler import (
    SchedulerEngine,
    SchedulerState,
    ScheduleType,
    Timer,
    is_session_boundary,
    next_session_boundary,
    session_timer,
)
from alphalab.scheduler.exceptions import SchedulerValidationError
from tests.unit.data.test_calendars import CME, NYSE, TSE, WEEKDAYS


def _at(year: int, month: int, day: int, hour: int, minute: int, zone: str) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(zone)).timestamp()


def _firings(state: SchedulerState, until: float, step: float = 900.0) -> list[float]:
    """Advance a quarter of an hour at a time, collecting each firing's clock reading."""

    fired: list[float] = []
    now = state.clock.current_time
    while now < until:
        now = min(now + step, until)
        target = min(timer.target_timestamp for timer in state.timers.values())
        state, triggered = SchedulerEngine.advance_clock(state, max(now, 0.0))
        if triggered:
            fired.append(target)
    return fired


def test_a_session_open_timer_fires_at_each_trading_days_open_and_skips_the_weekend() -> None:
    friday_evening = _at(2025, 11, 7, 18, 0, "America/New_York")
    timer = session_timer("open", ScheduleType.SESSION_OPEN, NYSE, friday_evening)
    assert timer.target_timestamp == _at(2025, 11, 10, 9, 30, "America/New_York")

    state = SchedulerEngine.schedule_timer(
        SchedulerEngine.initialize(friday_evening), timer, friday_evening
    )
    fired = _firings(state, _at(2025, 11, 14, 20, 0, "America/New_York"))

    assert fired == [_at(2025, 11, day, 9, 30, "America/New_York") for day in range(10, 15)]


def test_a_session_close_timer_fires_at_the_last_close_not_at_a_lunch_break() -> None:
    monday = _at(2025, 3, 3, 8, 0, "Asia/Tokyo")
    state = SchedulerEngine.schedule_timer(
        SchedulerEngine.initialize(monday),
        session_timer("close", ScheduleType.SESSION_CLOSE, TSE, monday),
        monday,
    )

    fired = _firings(state, _at(2025, 3, 4, 16, 0, "Asia/Tokyo"))

    assert fired == [_at(2025, 3, 3, 15, 0, "Asia/Tokyo"), _at(2025, 3, 4, 15, 0, "Asia/Tokyo")]


def test_an_overnight_session_opens_the_evening_before_and_closes_the_next_afternoon() -> None:
    noon = _at(2025, 6, 2, 12, 0, "America/Chicago")

    assert next_session_boundary(CME, noon, ScheduleType.SESSION_OPEN) == _at(
        2025, 6, 2, 17, 0, "America/Chicago"
    )
    assert next_session_boundary(CME, noon, ScheduleType.SESSION_CLOSE) == _at(
        2025, 6, 2, 16, 0, "America/Chicago"
    )
    # The close after that one ends the session that opened at 17:00.
    assert next_session_boundary(
        CME, _at(2025, 6, 2, 16, 0, "America/Chicago"), ScheduleType.SESSION_CLOSE
    ) == _at(2025, 6, 3, 16, 0, "America/Chicago")


def test_a_holiday_and_a_half_day_come_from_the_calendar() -> None:
    calendar = MarketCalendar(
        calendar_id="XTST",
        timezone_name="America/New_York",
        weekly_sessions={day: (SessionWindow(time(9, 30), time(16, 0)),) for day in WEEKDAYS},
        holidays=frozenset({date(2025, 11, 27)}),
        special_sessions={date(2025, 11, 28): (SessionWindow(time(9, 30), time(13, 0)),)},
    )
    wednesday_close = _at(2025, 11, 26, 16, 0, "America/New_York")

    assert next_session_boundary(calendar, wednesday_close, ScheduleType.SESSION_CLOSE) == _at(
        2025, 11, 28, 13, 0, "America/New_York"
    )


def test_a_boundary_is_recognised_exactly() -> None:
    opens = _at(2025, 11, 10, 9, 30, "America/New_York")

    assert is_session_boundary(NYSE, opens, ScheduleType.SESSION_OPEN)
    assert not is_session_boundary(NYSE, opens + 1.0, ScheduleType.SESSION_OPEN)
    assert not is_session_boundary(NYSE, opens, ScheduleType.SESSION_CLOSE)


@pytest.mark.parametrize(
    ("timer", "match"),
    [
        (Timer("t", 100.0, ScheduleType.SESSION_OPEN), "names no calendar"),
        (Timer("t", 100.0, ScheduleType.SESSION_CLOSE, calendar=NYSE), "is not a SESSION_CLOSE"),
        (Timer("t", 100.0, ScheduleType.ONE_SHOT, calendar=NYSE), "does not follow a calendar"),
    ],
)
def test_a_session_timer_that_does_not_follow_its_calendar_is_refused(
    timer: Timer, match: str
) -> None:
    with pytest.raises(SchedulerValidationError, match=match):
        SchedulerEngine.schedule_timer(SchedulerEngine.initialize(0.0), timer, 0.0)


def test_the_same_advancement_fires_the_same_way_twice() -> None:
    start = _at(2025, 3, 3, 8, 0, "Asia/Tokyo")
    until = _at(2025, 3, 7, 16, 0, "Asia/Tokyo")

    def run() -> list[float]:
        state = SchedulerEngine.initialize(start)
        for kind in (ScheduleType.SESSION_OPEN, ScheduleType.SESSION_CLOSE):
            state = SchedulerEngine.schedule_timer(
                state, session_timer(kind.name, kind, TSE, start), start
            )
        return _firings(state, until)

    assert run() == run()
    assert len(run()) == 10
