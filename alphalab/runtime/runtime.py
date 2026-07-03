"""Base instantiation point for a new Live Trading Runtime."""

from alphalab.runtime.lifecycle import RuntimeStatus
from alphalab.runtime.state import RuntimeState, SupervisorState


def create_runtime(runtime_id: str) -> RuntimeState:
    """Constructs a fresh, empty Runtime structure at the CREATED state."""
    if not runtime_id.strip():
        raise ValueError("Runtime ID cannot be empty.")

    return RuntimeState(
        runtime_id=runtime_id,
        status=RuntimeStatus.CREATED,
        supervisor=SupervisorState(),
    )
