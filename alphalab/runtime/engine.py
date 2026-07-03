"""Pure functional orchestration engine for the Live Trading Runtime."""

import uuid
from dataclasses import replace

from alphalab.runtime.events import (
    RuntimeFailed,
    RuntimePaused,
    RuntimeResumed,
    RuntimeStarted,
    RuntimeStopped,
)
from alphalab.runtime.lifecycle import RuntimeStatus
from alphalab.runtime.state import RuntimeState
from alphalab.runtime.supervisor import RuntimeSupervisor
from alphalab.runtime.validation import validate_transition


class RuntimeEngine:
    """Facade orchestrating pure functional state machine transitions for the framework."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def initialize(state: RuntimeState) -> RuntimeState:
        validate_transition(state.status, RuntimeStatus.INITIALIZED)
        return replace(state, status=RuntimeStatus.INITIALIZED)

    @staticmethod
    def start(state: RuntimeState, timestamp: float) -> RuntimeState:
        validate_transition(state.status, RuntimeStatus.STARTING)
        s1 = replace(state, status=RuntimeStatus.STARTING)
        
        validate_transition(s1.status, RuntimeStatus.RUNNING)
        start_evt = RuntimeStarted(
            RuntimeEngine._create_id(), timestamp, state.runtime_id
        )
        
        # Reset heartbeat clock on start
        new_sup = replace(state.supervisor, last_heartbeat=timestamp, is_healthy=True)

        return replace(
            s1,
            status=RuntimeStatus.RUNNING,
            supervisor=new_sup,
            events=(*state.events, start_evt),
        )

    @staticmethod
    def pause(state: RuntimeState, timestamp: float) -> RuntimeState:
        validate_transition(state.status, RuntimeStatus.PAUSED)
        pause_evt = RuntimePaused(
            RuntimeEngine._create_id(), timestamp, state.runtime_id
        )
        return replace(
            state, status=RuntimeStatus.PAUSED, events=(*state.events, pause_evt)
        )

    @staticmethod
    def resume(state: RuntimeState, timestamp: float) -> RuntimeState:
        validate_transition(state.status, RuntimeStatus.RUNNING)
        resume_evt = RuntimeResumed(
            RuntimeEngine._create_id(), timestamp, state.runtime_id
        )
        # Re-sync heartbeat
        new_sup = replace(state.supervisor, last_heartbeat=timestamp)
        return replace(
            state,
            status=RuntimeStatus.RUNNING,
            supervisor=new_sup,
            events=(*state.events, resume_evt),
        )

    @staticmethod
    def stop(state: RuntimeState, timestamp: float) -> RuntimeState:
        validate_transition(state.status, RuntimeStatus.STOPPING)
        s1 = replace(state, status=RuntimeStatus.STOPPING)
        
        validate_transition(s1.status, RuntimeStatus.STOPPED)
        stop_evt = RuntimeStopped(
            RuntimeEngine._create_id(), timestamp, state.runtime_id
        )
        return replace(
            s1, status=RuntimeStatus.STOPPED, events=(*state.events, stop_evt)
        )

    @staticmethod
    def fail(state: RuntimeState, reason: str, timestamp: float) -> RuntimeState:
        """Transitions directly to FAILED from any valid source state."""
        validate_transition(state.status, RuntimeStatus.FAILED)
        fail_evt = RuntimeFailed(
            RuntimeEngine._create_id(), timestamp, state.runtime_id, reason
        )
        
        new_sup = replace(state.supervisor, is_healthy=False)
        return replace(
            state,
            status=RuntimeStatus.FAILED,
            supervisor=new_sup,
            events=(*state.events, fail_evt),
        )

    @staticmethod
    def heartbeat(state: RuntimeState, timestamp: float) -> RuntimeState:
        """Records a heartbeat and verifies health limits."""
        # Check health prior to registering the new timestamp to accurately evaluate timeout
        s1 = RuntimeSupervisor.check_health(state, timestamp)
        
        active_states = {RuntimeStatus.RUNNING, RuntimeStatus.PAUSED}
        if not s1.supervisor.is_healthy and s1.status in active_states:
            return RuntimeEngine.fail(s1, "Missed heartbeat threshold exceeded.", timestamp)
            
        # Only log the heartbeat if a failure threshold hasn't been breached
        return RuntimeSupervisor.register_heartbeat(s1, timestamp)