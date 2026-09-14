"""Global immutable state container for the Scheduler Engine.

The keyed indexes and the event log use the canonical containers from
:mod:`alphalab.common`, for the reason v2.1 and v2.2 introduced them.

Until v2.17 every mutator here rebuilt a whole ``dict`` and a whole ``tuple``
per transition -- ``dict(state.timers)``, ``(*state.events, event)`` -- so
registering ``N`` timers copied ``O(N^2)`` entries twice over. ADR-0032
classified this as category C finding 1 and measured it: registering timers grew
at ~3.2-3.8x per doubling from 2,500 to 20,000, where linear is ~2x.

ADR-0034 takes the fix. No new mechanism is introduced and no semantics change:
:class:`~alphalab.common.append_log.AppendOnlyLog` and
:class:`~alphalab.common.persistent_map.PersistentMap` are the containers the
execution path has used since v2.1 and v2.2, both are immutable, both define
value equality, and both iterate deterministically -- a ``PersistentMap`` in
first-insertion order of the keys still present, which is what a ``dict``
does too.
"""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.scheduler.clock import ClockState
from alphalab.scheduler.events import SchedulerEvent
from alphalab.scheduler.session import TradingSession
from alphalab.scheduler.timer import Timer


@dataclass(frozen=True, slots=True)
class SchedulerState:
    """Deterministic snapshot of time, timers, and active sessions."""

    clock: ClockState
    timers: PersistentMap[str, Timer] = field(default_factory=PersistentMap)
    active_sessions: PersistentMap[str, TradingSession] = field(default_factory=PersistentMap)
    events: AppendOnlyLog[SchedulerEvent] = field(default_factory=AppendOnlyLog)
