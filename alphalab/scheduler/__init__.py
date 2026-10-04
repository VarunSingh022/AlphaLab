"""AlphaLab Scheduler & Time Engine: deterministic timers.

One-shot, interval and -- since v3.12 -- session timers, which fire at a market's
trading-day opens and closes as its :class:`~alphalab.data.calendar.MarketCalendar`
declares them (ledger SCF-003). The engine is driven by
:meth:`SchedulerEngine.advance_clock` with instants the caller supplies, so a
schedule replays identically; ``SystemClock`` is the one wall clock here and the
engine never reads it.
"""

from alphalab.scheduler.clock import (
    BacktestClock,
    ClockProtocol,
    ClockState,
    SystemClock,
    VirtualClock,
)
from alphalab.scheduler.engine import SchedulerEngine
from alphalab.scheduler.events import (
    ClockAdvanced,
    ClockReset,
    SchedulerEvent,
    SessionEnded,
    SessionStarted,
    TimerCancelled,
    TimerScheduled,
    TimerTriggered,
)
from alphalab.scheduler.exceptions import (
    InvalidClockStateError,
    SchedulerError,
    SchedulerValidationError,
)
from alphalab.scheduler.schedule import ScheduleType
from alphalab.scheduler.scheduler import (
    SESSION_SCHEDULES,
    SchedulerResolver,
    is_session_boundary,
    next_session_boundary,
    session_timer,
)
from alphalab.scheduler.session import ScheduledSession, SessionPhase
from alphalab.scheduler.state import SchedulerState
from alphalab.scheduler.timer import Timer
from alphalab.scheduler.validation import validate_timer
from alphalab.scheduler.views import active_sessions, current_time, next_timer, scheduled_timers

__all__ = [
    "SESSION_SCHEDULES",
    "BacktestClock",
    "ClockAdvanced",
    "ClockProtocol",
    "ClockReset",
    "ClockState",
    "InvalidClockStateError",
    "ScheduleType",
    "ScheduledSession",
    "SchedulerEngine",
    "SchedulerError",
    "SchedulerEvent",
    "SchedulerResolver",
    "SchedulerState",
    "SchedulerValidationError",
    "SessionEnded",
    "SessionPhase",
    "SessionStarted",
    "SystemClock",
    "Timer",
    "TimerCancelled",
    "TimerScheduled",
    "TimerTriggered",
    "VirtualClock",
    "active_sessions",
    "current_time",
    "is_session_boundary",
    "next_session_boundary",
    "next_timer",
    "scheduled_timers",
    "session_timer",
    "validate_timer",
]
