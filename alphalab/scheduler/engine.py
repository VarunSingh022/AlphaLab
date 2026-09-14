"""Pure functional Scheduler Engine orchestrating time progression."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.common.persistent_map import PersistentMap
from alphalab.scheduler.clock import ClockState
from alphalab.scheduler.events import (
    ClockAdvanced,
    ClockReset,
    SessionEnded,
    SessionStarted,
    TimerCancelled,
    TimerScheduled,
    TimerTriggered,
)
from alphalab.scheduler.exceptions import InvalidClockStateError
from alphalab.scheduler.scheduler import SchedulerResolver
from alphalab.scheduler.session import TradingSession
from alphalab.scheduler.state import SchedulerState
from alphalab.scheduler.timer import Timer
from alphalab.scheduler.validation import validate_timer


class SchedulerEngine:
    """Stateless functional engine orchestrating deterministic time progression."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def initialize(initial_time: float) -> SchedulerState:
        """Creates a fresh scheduler state anchored at a specific point in time."""
        return SchedulerState(clock=ClockState(current_time=initial_time))

    @staticmethod
    def schedule_timer(state: SchedulerState, timer: Timer, timestamp: float) -> SchedulerState:
        """Validates and registers a new timer."""
        if timer.timer_id in state.timers:
            raise ValueError(f"Duplicate timer ID: {timer.timer_id}")

        validate_timer(timer, state.clock.current_time)

        event = TimerScheduled(
            event_id=SchedulerEngine._create_id(),
            timestamp=timestamp,
            timer_id=timer.timer_id,
            trigger_time=timer.target_timestamp,
            schedule_type=timer.schedule_type.name,
        )

        return replace(
            state,
            timers=state.timers.set(timer.timer_id, timer),
            events=state.events.append(event),
        )

    @staticmethod
    def cancel_timer(state: SchedulerState, timer_id: str, timestamp: float) -> SchedulerState:
        """Removes an active timer from the scheduler."""
        if timer_id not in state.timers:
            return state

        event = TimerCancelled(
            event_id=SchedulerEngine._create_id(),
            timestamp=timestamp,
            timer_id=timer_id,
        )

        return replace(
            state,
            timers=state.timers.delete(timer_id),
            events=state.events.append(event),
        )

    @staticmethod
    def advance_clock(
        state: SchedulerState, new_time: float
    ) -> tuple[SchedulerState, tuple[TimerTriggered, ...]]:
        """
        Advances the clock and triggers all eligible timers deterministically.
        Returns the updated state and an ordered tuple of TimerTriggered events.
        """
        old_time = state.clock.current_time
        if new_time < old_time:
            raise InvalidClockStateError("Clock cannot move backwards.")

        if new_time == old_time:
            return state, ()

        # 1. Update Clock
        advance_evt = ClockAdvanced(
            event_id=SchedulerEngine._create_id(),
            timestamp=new_time,
            old_time=old_time,
            new_time=new_time,
        )

        triggered: list[TimerTriggered] = []
        events = state.events.append(advance_evt)

        # A **batch** operation over the whole timer set in one call, not an
        # accumulation across calls -- so one local copy in and one immutable
        # value out is the right shape, exactly as it is for
        # ``alphalab.distributed.scheduler.JobScheduler.assign_jobs``. Writing
        # each expiry through ``PersistentMap.set`` instead would pay the
        # persistent container's per-write cost once per *timer* rather than
        # once per *call*, which measured ~30% of this path at 100,000 timers.
        # The accumulation paths -- schedule_timer, cancel_timer -- are the ones
        # that had to become O(1), and they did.
        new_timers = dict(state.timers)

        # 2. Sort by target_timestamp to guarantee strict deterministic execution order
        sorted_timers = sorted(
            state.timers.values(), key=lambda t: (t.target_timestamp, t.timer_id)
        )

        for timer in sorted_timers:
            if timer.target_timestamp <= new_time:
                trigger_evt = TimerTriggered(
                    event_id=SchedulerEngine._create_id(),
                    timestamp=new_time,
                    timer_id=timer.timer_id,
                )
                triggered.append(trigger_evt)
                events = events.append(trigger_evt)

                # 3. Resolve repeats
                next_timer = SchedulerResolver.resolve_next_timer(timer, new_time)
                if next_timer is not None:
                    new_timers[timer.timer_id] = next_timer
                else:
                    del new_timers[timer.timer_id]

        if not triggered:
            # Nothing expired, so the timer map is what it was. Rebuilding it
            # from the local copy would discard the shared structure for nothing.
            return replace(state, clock=ClockState(current_time=new_time), events=events), ()

        new_state = replace(
            state,
            clock=ClockState(current_time=new_time),
            timers=PersistentMap(new_timers),
            events=events,
        )

        return new_state, tuple(triggered)

    @staticmethod
    def start_session(
        state: SchedulerState, session: TradingSession, timestamp: float
    ) -> SchedulerState:
        """Registers a new trading session as active."""
        if session.session_id in state.active_sessions:
            raise ValueError(f"Session {session.session_id} is already active.")

        event = SessionStarted(
            event_id=SchedulerEngine._create_id(),
            timestamp=timestamp,
            session_id=session.session_id,
            phase=session.phase.name,
        )
        return replace(
            state,
            active_sessions=state.active_sessions.set(session.session_id, session),
            events=state.events.append(event),
        )

    @staticmethod
    def end_session(state: SchedulerState, session_id: str, timestamp: float) -> SchedulerState:
        """Closes an active trading session."""
        if session_id not in state.active_sessions:
            return state

        event = SessionEnded(
            event_id=SchedulerEngine._create_id(),
            timestamp=timestamp,
            session_id=session_id,
        )
        return replace(
            state,
            active_sessions=state.active_sessions.delete(session_id),
            events=state.events.append(event),
        )

    @staticmethod
    def reset_clock(state: SchedulerState, reset_time: float) -> SchedulerState:
        """Forcibly resets the clock and clears active sessions (for replay sync)."""
        event = ClockReset(
            event_id=SchedulerEngine._create_id(),
            timestamp=reset_time,
            reset_time=reset_time,
        )
        return replace(
            state,
            clock=ClockState(current_time=reset_time),
            active_sessions=PersistentMap(),
            events=state.events.append(event),
        )
