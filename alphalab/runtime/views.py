"""Pure queries exposing transparent Runtime logic access."""

from alphalab.runtime.lifecycle import RuntimeStatus
from alphalab.runtime.metrics import RuntimeMetrics
from alphalab.runtime.state import RuntimeState


def runtime_status(state: RuntimeState) -> RuntimeStatus:
    """Returns the current explicit lifecycle state of the runtime."""
    return state.status


def runtime_metrics(state: RuntimeState) -> RuntimeMetrics:
    """Returns the immutable metrics summary."""
    return state.metrics


def uptime(state: RuntimeState) -> float:
    """Returns the active processing uptime in seconds."""
    return state.metrics.uptime_seconds


def dispatcher_statistics(state: RuntimeState) -> tuple[int, float, float]:
    """Returns core dispatch telemetry: (processed, errors, ops_per_sec)."""
    return (
        state.metrics.events_processed,
        state.metrics.error_count,
        state.metrics.events_per_second,
    )