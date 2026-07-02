"""Pure queries exposing transparent Scheduler access."""

from collections.abc import Sequence

from alphalab.scheduler.session import TradingSession
from alphalab.scheduler.state import SchedulerState
from alphalab.scheduler.timer import Timer


def scheduled_timers(state: SchedulerState) -> Sequence[Timer]:
    """Returns all currently active registered timers."""
    return tuple(state.timers.values())


def active_sessions(state: SchedulerState) -> Sequence[TradingSession]:
    """Returns all currently open trading sessions."""
    return tuple(state.active_sessions.values())


def current_time(state: SchedulerState) -> float:
    """Returns the current deterministic time of the engine."""
    return state.clock.current_time


def next_timer(state: SchedulerState) -> Timer | None:
    """Returns the timer scheduled to trigger next."""
    if not state.timers:
        return None
    return min(state.timers.values(), key=lambda t: t.target_timestamp)
