"""Pure functional Replay Engine orchestrating historical event sequences."""

import uuid
from dataclasses import replace

from alphalab.replay.events import (
    ReplayAdvanced,
    ReplayCompleted,
    ReplayPaused,
    ReplayReset,
    ReplayResumed,
    ReplayStarted,
    ReplayStopped,
)
from alphalab.replay.exceptions import InvalidReplayStateError
from alphalab.replay.loader import HistoricalEventProtocol
from alphalab.replay.replay import ReplayBatchResult, ReplayStepResult
from alphalab.replay.session import ReplaySession
from alphalab.replay.state import ReplayState, ReplayStatus
from alphalab.replay.validation import validate_events, validate_session


class ReplayEngine:
    """Stateless functional engine orchestrating deterministic replay progression."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def initialize(
        session: ReplaySession, events: tuple[HistoricalEventProtocol, ...], real_time: float
    ) -> ReplayState:
        """Validates inputs and constructs a fresh READY replay state."""
        validate_session(session)
        validate_events(events)

        return ReplayState(
            session=session,
            status=ReplayStatus.READY,
            events=events,
            current_index=0,
            current_timestamp=session.start_time,
            real_start_time=real_time,
            real_current_time=real_time,
            system_events=(),
        )

    @staticmethod
    def start(state: ReplayState, real_time: float) -> ReplayState:
        """Transitions from READY to RUNNING and logs the start event."""
        if state.status != ReplayStatus.READY:
            raise InvalidReplayStateError(f"Cannot start from {state.status.name}")

        start_evt = ReplayStarted(
            ReplayEngine._create_id(),
            state.current_timestamp,
            state.session.session_id,
            len(state.events),
        )

        return replace(
            state,
            status=ReplayStatus.RUNNING,
            real_start_time=real_time,
            real_current_time=real_time,
            system_events=(*state.system_events, start_evt),
        )

    @staticmethod
    def pause(state: ReplayState, real_time: float) -> ReplayState:
        """Pauses a RUNNING replay."""
        if state.status != ReplayStatus.RUNNING:
            raise InvalidReplayStateError(f"Cannot pause from {state.status.name}")

        pause_evt = ReplayPaused(
            ReplayEngine._create_id(), state.current_timestamp, state.session.session_id
        )

        return replace(
            state,
            status=ReplayStatus.PAUSED,
            real_current_time=real_time,
            system_events=(*state.system_events, pause_evt),
        )

    @staticmethod
    def resume(state: ReplayState, real_time: float) -> ReplayState:
        """Resumes a PAUSED replay."""
        if state.status != ReplayStatus.PAUSED:
            raise InvalidReplayStateError(f"Cannot resume from {state.status.name}")

        resume_evt = ReplayResumed(
            ReplayEngine._create_id(), state.current_timestamp, state.session.session_id
        )

        return replace(
            state,
            status=ReplayStatus.RUNNING,
            real_current_time=real_time,
            system_events=(*state.system_events, resume_evt),
        )

    @staticmethod
    def stop(state: ReplayState, real_time: float) -> ReplayState:
        """Aborts a replay sequence prematurely."""
        if state.status in {ReplayStatus.STOPPED, ReplayStatus.COMPLETED}:
            raise InvalidReplayStateError("Already stopped or completed.")

        stop_evt = ReplayStopped(
            ReplayEngine._create_id(), state.current_timestamp, state.session.session_id
        )

        return replace(
            state,
            status=ReplayStatus.STOPPED,
            real_current_time=real_time,
            system_events=(*state.system_events, stop_evt),
        )

    @staticmethod
    def step_one_event(state: ReplayState, real_time: float) -> ReplayStepResult:
        """Pops and yields exactly one historical event from the sequence."""
        if state.status != ReplayStatus.RUNNING:
            return ReplayStepResult(replace(state, real_current_time=real_time), None)

        if state.current_index >= len(state.events):
            comp_evt = ReplayCompleted(
                ReplayEngine._create_id(), state.current_timestamp, state.session.session_id
            )
            new_state = replace(
                state,
                status=ReplayStatus.COMPLETED,
                real_current_time=real_time,
                system_events=(*state.system_events, comp_evt),
            )
            return ReplayStepResult(new_state, None)

        event = state.events[state.current_index]
        new_idx = state.current_index + 1

        adv_evt = ReplayAdvanced(
            ReplayEngine._create_id(), event.timestamp, state.current_timestamp, event.timestamp, 1
        )

        new_state = replace(
            state,
            current_index=new_idx,
            current_timestamp=event.timestamp,
            real_current_time=real_time,
            system_events=(*state.system_events, adv_evt),
        )

        if new_idx >= len(state.events):
            comp_evt = ReplayCompleted(
                ReplayEngine._create_id(), event.timestamp, state.session.session_id
            )
            new_state = replace(
                new_state,
                status=ReplayStatus.COMPLETED,
                system_events=(*new_state.system_events, comp_evt),
            )

        return ReplayStepResult(new_state, event)

    @staticmethod
    def step_timestamp(
        state: ReplayState, target_timestamp: float, real_time: float
    ) -> ReplayBatchResult:
        """Yields all historical events chronologically up to the target timestamp."""
        if state.status != ReplayStatus.RUNNING:
            return ReplayBatchResult(replace(state, real_current_time=real_time), ())

        if state.current_index >= len(state.events):
            return ReplayBatchResult(replace(state, real_current_time=real_time), ())

        batch = []
        idx = state.current_index
        events = state.events
        length = len(events)

        start_ts = state.current_timestamp

        while idx < length and events[idx].timestamp <= target_timestamp:
            batch.append(events[idx])
            idx += 1

        if not batch:
            # Replay time progressed but no events existed in window
            new_state = replace(
                state, current_timestamp=target_timestamp, real_current_time=real_time
            )
            return ReplayBatchResult(new_state, ())

        adv_evt = ReplayAdvanced(
            ReplayEngine._create_id(),
            target_timestamp,
            start_ts,
            target_timestamp,
            len(batch),
        )
        new_state = replace(
            state,
            current_index=idx,
            current_timestamp=target_timestamp,
            real_current_time=real_time,
            system_events=(*state.system_events, adv_evt),
        )

        if idx >= length:
            comp_evt = ReplayCompleted(
                ReplayEngine._create_id(), target_timestamp, state.session.session_id
            )
            new_state = replace(
                new_state,
                status=ReplayStatus.COMPLETED,
                system_events=(*new_state.system_events, comp_evt),
            )

        return ReplayBatchResult(new_state, tuple(batch))

    @staticmethod
    def reset(state: ReplayState, real_time: float) -> ReplayState:
        """Reverts the entire replay progression back to its start configuration."""
        reset_evt = ReplayReset(ReplayEngine._create_id(), real_time, real_time)
        return replace(
            state,
            status=ReplayStatus.READY,
            current_index=0,
            current_timestamp=state.session.start_time,
            real_start_time=real_time,
            real_current_time=real_time,
            system_events=(*state.system_events, reset_evt),
        )
