"""Validation rules for lifecycle transitions and dispatching."""

from alphalab.runtime.exceptions import InvalidRuntimeTransitionError, RuntimeValidationError
from alphalab.runtime.lifecycle import RuntimeStatus
from alphalab.runtime.state import RuntimeState


def validate_transition(current: RuntimeStatus, target: RuntimeStatus) -> None:
    """Validates structural state machine transitions."""
    valid_transitions = {
        RuntimeStatus.CREATED: {RuntimeStatus.INITIALIZED, RuntimeStatus.FAILED},
        RuntimeStatus.INITIALIZED: {RuntimeStatus.STARTING, RuntimeStatus.FAILED},
        RuntimeStatus.STARTING: {RuntimeStatus.RUNNING, RuntimeStatus.FAILED},
        RuntimeStatus.RUNNING: {RuntimeStatus.PAUSED, RuntimeStatus.STOPPING, RuntimeStatus.FAILED},
        RuntimeStatus.PAUSED: {RuntimeStatus.RUNNING, RuntimeStatus.STOPPING, RuntimeStatus.FAILED},
        RuntimeStatus.STOPPING: {RuntimeStatus.STOPPED, RuntimeStatus.FAILED},
        RuntimeStatus.STOPPED: {RuntimeStatus.INITIALIZED},  # Can be re-initialized
        RuntimeStatus.FAILED: {RuntimeStatus.INITIALIZED},  # Can recover via re-initialization
    }

    allowed = valid_transitions.get(current, set())
    if target not in allowed:
        raise InvalidRuntimeTransitionError(
            f"Cannot transition from {current.name} to {target.name}."
        )


def validate_dispatch(state: RuntimeState) -> None:
    """Ensures events are only dispatched when the runtime is actively processing."""
    if state.status != RuntimeStatus.RUNNING:
        raise InvalidRuntimeTransitionError(
            f"Cannot dispatch events while runtime is in {state.status.name}."
        )


def validate_heartbeat_config(interval: float, max_missed: int) -> None:
    """Validates supervisor heartbeat bounds."""
    if interval <= 0.0:
        raise RuntimeValidationError("Heartbeat interval must be strictly positive.")
    if max_missed <= 0:
        raise RuntimeValidationError("Max missed heartbeats must be strictly positive.")
