"""Every timer type the scheduler declares, it keeps (ledger DAT-006).

``ScheduleType`` declared CRON, SESSION_OPEN, SESSION_CLOSE and BAR_BOUNDARY,
and until v3.10 nothing implemented them: a timer of one of those types was
accepted, fired once at its target, and was never rescheduled -- the audit's
probe registered a CRON timer and found none left after its first firing. v3.10
refused them at registration; v3.12 implemented SESSION_OPEN and SESSION_CLOSE
over the venue's ``MarketCalendar`` (ledger SCF-003); v3.13 implemented CRON
over a ``CronSchedule`` and removed BAR_BOUNDARY, which restated a convention of
the data. So no declared type is left unimplemented, and the probe that found
the defect now finds every repeating timer still registered after it fires.
"""

from datetime import date, time

import pytest

from alphalab.data import MarketCalendar, SessionWindow
from alphalab.scheduler import (
    CronSchedule,
    SchedulerEngine,
    ScheduleType,
    Timer,
    cron_timer,
    session_timer,
)

CALENDAR = MarketCalendar(
    "XTST",
    "UTC",
    {day: (SessionWindow(time(9, 0), time(17, 0)),) for day in range(5)},
)
#: Monday 5 January 2026, 00:00 UTC.
MONDAY = 1_767_571_200.0


def _timer(kind: ScheduleType) -> Timer:
    if kind in (ScheduleType.SESSION_OPEN, ScheduleType.SESSION_CLOSE):
        return session_timer("T", kind, CALENDAR, MONDAY)
    if kind is ScheduleType.CRON:
        return cron_timer("T", CronSchedule("0 * * * *", "UTC"), MONDAY)
    interval = 3_600.0 if kind in (ScheduleType.INTERVAL, ScheduleType.REPEATING) else None
    return Timer("T", MONDAY + 3_600.0, kind, interval=interval)


def test_bar_boundary_is_no_longer_a_schedule_type() -> None:
    assert "BAR_BOUNDARY" not in ScheduleType.__members__


@pytest.mark.parametrize("kind", list(ScheduleType), ids=lambda kind: kind.name)
def test_every_declared_type_registers_and_fires(kind: ScheduleType) -> None:
    state = SchedulerEngine.schedule_timer(SchedulerEngine.initialize(MONDAY), _timer(kind), MONDAY)
    target = state.timers["T"].target_timestamp

    state, triggered = SchedulerEngine.advance_clock(state, target)

    assert [event.timer_id for event in triggered] == ["T"]
    repeats = kind not in (ScheduleType.ONE_SHOT, ScheduleType.MANUAL)
    assert ("T" in state.timers) is repeats, "a repeating timer stays registered after it fires"
    if repeats:
        assert state.timers["T"].target_timestamp > target


def test_the_calendar_used_here_trades_on_weekdays() -> None:
    assert CALENDAR.session_bounds(date(2026, 1, 5)) is not None
    assert CALENDAR.session_bounds(date(2026, 1, 10)) is None
