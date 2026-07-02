"""Global immutable state container for the Scheduler Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.scheduler.clock import ClockState
from alphalab.scheduler.events import SchedulerEvent
from alphalab.scheduler.session import TradingSession
from alphalab.scheduler.timer import Timer


@dataclass(frozen=True, slots=True)
class SchedulerState:
    """Deterministic snapshot of time, timers, and active sessions."""

    clock: ClockState
    timers: Mapping[str, Timer] = field(default_factory=dict)
    active_sessions: Mapping[str, TradingSession] = field(default_factory=dict)
    events: tuple[SchedulerEvent, ...] = field(default_factory=tuple)
