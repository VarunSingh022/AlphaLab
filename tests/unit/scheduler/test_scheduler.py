"""Comprehensive unit tests covering time progression and event generation."""

import pytest

from alphalab.scheduler import (
    ClockProtocol,
    InvalidClockStateError,
    SchedulerEngine,
    SchedulerValidationError,
    ScheduleType,
    SessionPhase,
    Timer,
    TradingCalendar,
    TradingSession,
    VirtualClock,
    active_sessions,
    current_time,
    next_timer,
    scheduled_timers,
)


def test_virtual_clock_immutability() -> None:
    clock: ClockProtocol = VirtualClock(1000.0)
    assert clock.now() == 1000.0


def test_timer_validation() -> None:
    # Cannot schedule in the past
    with pytest.raises(SchedulerValidationError, match="in the past"):
        t_past = Timer("T1", 900.0, ScheduleType.ONE_SHOT)
        SchedulerEngine.schedule_timer(SchedulerEngine.initialize(1000.0), t_past, 1000.0)

    # Repeating requires interval
    with pytest.raises(SchedulerValidationError, match="positive interval"):
        t_repeat = Timer("T2", 1100.0, ScheduleType.REPEATING, interval=-5.0)
        SchedulerEngine.schedule_timer(SchedulerEngine.initialize(1000.0), t_repeat, 1000.0)


def test_schedule_and_cancel_timer() -> None:
    state = SchedulerEngine.initialize(1000.0)
    t = Timer("T1", 1100.0, ScheduleType.ONE_SHOT)

    state = SchedulerEngine.schedule_timer(state, t, 1000.0)
    assert len(scheduled_timers(state)) == 1
    assert next_timer(state) == t

    state = SchedulerEngine.cancel_timer(state, "T1", 1005.0)
    assert len(scheduled_timers(state)) == 0


def test_clock_advance_triggers_timer() -> None:
    state = SchedulerEngine.initialize(1000.0)
    t = Timer("T1", 1100.0, ScheduleType.ONE_SHOT)
    state = SchedulerEngine.schedule_timer(state, t, 1000.0)

    # Advance just before trigger
    state, events = SchedulerEngine.advance_clock(state, 1099.0)
    assert len(events) == 0
    assert len(scheduled_timers(state)) == 1

    # Advance past trigger
    state, events = SchedulerEngine.advance_clock(state, 1105.0)
    assert len(events) == 1
    assert events[0].timer_id == "T1"

    # One-shot is removed
    assert len(scheduled_timers(state)) == 0


def test_clock_advance_repeating_timer() -> None:
    state = SchedulerEngine.initialize(1000.0)
    t = Timer("T_REP", 1100.0, ScheduleType.REPEATING, interval=50.0)
    state = SchedulerEngine.schedule_timer(state, t, 1000.0)

    # Advance exactly to trigger
    state, events = SchedulerEngine.advance_clock(state, 1100.0)
    assert len(events) == 1
    assert events[0].timer_id == "T_REP"

    # Timer should be rescheduled
    nxt = next_timer(state)
    assert nxt is not None
    assert nxt.target_timestamp == 1150.0

    # Advance multiple intervals in one large jump
    state, events = SchedulerEngine.advance_clock(state, 1260.0)
    assert len(events) == 1
    assert events[0].timer_id == "T_REP"

    # New target should be correctly offset
    nxt = next_timer(state)
    assert nxt is not None
    assert nxt.target_timestamp == 1300.0


def test_invalid_clock_operations() -> None:
    state = SchedulerEngine.initialize(1000.0)
    with pytest.raises(InvalidClockStateError, match="backwards"):
        SchedulerEngine.advance_clock(state, 900.0)


def test_session_management() -> None:
    state = SchedulerEngine.initialize(1000.0)
    session = TradingSession("SESS-1", 1000.0, 5000.0, SessionPhase.REGULAR_SESSION)

    state = SchedulerEngine.start_session(state, session, 1000.0)
    assert len(active_sessions(state)) == 1

    state = SchedulerEngine.end_session(state, "SESS-1", 5000.0)
    assert len(active_sessions(state)) == 0


def test_trading_calendar() -> None:
    # 2024-01-06 is a Saturday
    saturday_ts = 1704542400.0
    assert TradingCalendar.is_weekend(saturday_ts) is True
    assert TradingCalendar.is_trading_day(saturday_ts) is False

    # 2024-01-08 is a Monday
    monday_ts = 1704715200.0
    assert TradingCalendar.is_weekend(monday_ts) is False
    assert TradingCalendar.is_trading_day(monday_ts) is True

    # Next session skips Sunday
    next_sess = TradingCalendar.next_trading_session(saturday_ts)
    assert TradingCalendar.is_weekend(next_sess) is False


def test_clock_reset() -> None:
    state = SchedulerEngine.initialize(1000.0)
    state = SchedulerEngine.schedule_timer(
        state, Timer("T1", 1500.0, ScheduleType.ONE_SHOT), 1000.0
    )
    session = TradingSession("S1", 1000.0, 5000.0, SessionPhase.REGULAR_SESSION)
    state = SchedulerEngine.start_session(state, session, 1000.0)

    # Force reset
    state = SchedulerEngine.reset_clock(state, 0.0)
    assert current_time(state) == 0.0
    assert len(active_sessions(state)) == 0
    # Timers persist across pure time resets, but output events record the discontinuity
    assert len(scheduled_timers(state)) == 1
    assert any(type(e).__name__ == "ClockReset" for e in state.events)
