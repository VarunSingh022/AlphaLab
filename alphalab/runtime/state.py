"""Global immutable state container for the Runtime layer."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.runtime.events import RuntimeEvent
from alphalab.runtime.lifecycle import RuntimeStatus
from alphalab.runtime.metrics import RuntimeMetrics


@dataclass(frozen=True, slots=True)
class SupervisorState:
    """Immutable tracking of health and failure policies."""
    last_heartbeat: float = 0.0
    heartbeat_interval: float = 1.0
    max_missed_heartbeats: int = 3
    is_healthy: bool = True


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Deterministic snapshot of the orchestration layer."""
    runtime_id: str
    status: RuntimeStatus
    supervisor: SupervisorState
    metrics: RuntimeMetrics = field(default_factory=RuntimeMetrics)
    metadata: Mapping[str, str] = field(default_factory=dict)
    events: tuple[RuntimeEvent, ...] = field(default_factory=tuple)