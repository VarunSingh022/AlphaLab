"""Health monitoring and failure propagation logic."""

import uuid
from dataclasses import replace

from alphalab.runtime.events import Heartbeat
from alphalab.runtime.state import RuntimeState
from alphalab.runtime.validation import validate_heartbeat_config


class RuntimeSupervisor:
    """Stateless evaluator of runtime health and heartbeat constraints."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def configure(interval: float, max_missed: int) -> tuple[float, int]:
        """Validates bounds for configuring a SupervisorState."""
        validate_heartbeat_config(interval, max_missed)
        return interval, max_missed

    @staticmethod
    def register_heartbeat(state: RuntimeState, timestamp: float) -> RuntimeState:
        """Records a successful keep-alive ping."""
        hb_evt = Heartbeat(
            event_id=RuntimeSupervisor._create_id(),
            timestamp=timestamp,
            runtime_id=state.runtime_id,
        )

        new_supervisor = replace(state.supervisor, last_heartbeat=timestamp, is_healthy=True)

        new_uptime = max(
            state.metrics.uptime_seconds,
            timestamp - state.supervisor.last_heartbeat,
        )

        new_metrics = replace(
            state.metrics,
            heartbeat_count=state.metrics.heartbeat_count + 1,
            uptime_seconds=new_uptime,
        )

        return replace(
            state,
            supervisor=new_supervisor,
            metrics=new_metrics,
            events=(*state.events, hb_evt),
        )

    @staticmethod
    def check_health(state: RuntimeState, timestamp: float) -> RuntimeState:
        """Evaluates whether the runtime has missed too many heartbeats."""
        time_since_last = timestamp - state.supervisor.last_heartbeat
        allowed_window = (
            state.supervisor.heartbeat_interval * state.supervisor.max_missed_heartbeats
        )

        is_healthy = time_since_last <= allowed_window

        if is_healthy == state.supervisor.is_healthy:
            return state

        new_supervisor = replace(state.supervisor, is_healthy=is_healthy)
        return replace(state, supervisor=new_supervisor)
