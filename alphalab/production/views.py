"""Pure queries exposing transparent Production State access."""

from collections.abc import Sequence

from alphalab.production.checkpoint import Checkpoint
from alphalab.production.health import SystemHealth
from alphalab.production.heartbeat import HeartbeatRecord
from alphalab.production.metrics import RuntimeMetrics
from alphalab.production.monitor import Alert
from alphalab.production.process import ManagedProcess
from alphalab.production.state import ProductionState


def runtime_summary(state: ProductionState) -> dict[str, str | float | bool]:
    """Returns general overview of runtime liveness."""
    return {
        "runtime_id": state.runtime_id,
        "is_running": state.is_running,
        "uptime": state.uptime,
        "total_modules": len(state.processes),
    }


def health_report(state: ProductionState) -> SystemHealth | None:
    return state.health


def heartbeat_status(state: ProductionState) -> Sequence[HeartbeatRecord]:
    return tuple(state.heartbeats.values())


def checkpoint_history(state: ProductionState) -> Sequence[Checkpoint]:
    return state.checkpoints


def alert_summary(state: ProductionState) -> Sequence[Alert]:
    return tuple(a for a in state.alerts if a.active)


def runtime_metrics(state: ProductionState) -> RuntimeMetrics:
    return state.metrics


def get_module(state: ProductionState, module_id: str) -> ManagedProcess | None:
    return state.processes.get(module_id)
