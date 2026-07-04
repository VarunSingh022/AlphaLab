"""Global immutable state container for the Production Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.production.checkpoint import Checkpoint
from alphalab.production.events import ProductionEvent
from alphalab.production.health import SystemHealth
from alphalab.production.heartbeat import HeartbeatRecord
from alphalab.production.logging import LogEntry
from alphalab.production.metrics import RuntimeMetrics
from alphalab.production.monitor import Alert
from alphalab.production.process import ManagedProcess


@dataclass(frozen=True, slots=True)
class ProductionState:
    """Deterministic snapshot of the Production Runtime environment."""

    runtime_id: str
    is_running: bool = False
    start_time: float = 0.0
    uptime: float = 0.0
    last_tick: float = 0.0
    health: SystemHealth | None = None
    metrics: RuntimeMetrics = field(default_factory=RuntimeMetrics)
    processes: Mapping[str, ManagedProcess] = field(default_factory=dict)
    heartbeats: Mapping[str, HeartbeatRecord] = field(default_factory=dict)
    alerts: tuple[Alert, ...] = field(default_factory=tuple)
    checkpoints: tuple[Checkpoint, ...] = field(default_factory=tuple)
    logs: tuple[LogEntry, ...] = field(default_factory=tuple)
    events: tuple[ProductionEvent, ...] = field(default_factory=tuple)
