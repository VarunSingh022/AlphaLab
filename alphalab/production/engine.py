"""Top-level Engine Facade orchestrating Production Clusters."""

from alphalab.production.checkpoint import Checkpoint
from alphalab.production.logging import LogEntry
from alphalab.production.recovery import RecoveryEngine
from alphalab.production.runtime import RuntimeOperations
from alphalab.production.scheduler import RuntimeScheduler
from alphalab.production.state import ProductionState
from alphalab.production.supervisor import Supervisor


class ProductionEngine:
    """Facade for managing deterministic production orchestration."""

    @staticmethod
    def initialize(runtime_id: str) -> ProductionState:
        if not runtime_id.strip():
            raise ValueError("Runtime ID cannot be empty.")
        return ProductionState(runtime_id=runtime_id)

    @staticmethod
    def start(state: ProductionState, timestamp: float) -> ProductionState:
        return RuntimeOperations.start(state, timestamp)

    @staticmethod
    def stop(state: ProductionState, reason: str, timestamp: float) -> ProductionState:
        return RuntimeOperations.stop(state, reason, timestamp)

    @staticmethod
    def tick(state: ProductionState, timestamp: float) -> ProductionState:
        return RuntimeScheduler.tick(state, timestamp)

    @staticmethod
    def heartbeat(
        state: ProductionState, module_id: str, latency: float, timestamp: float
    ) -> ProductionState:
        return RuntimeOperations.heartbeat(state, module_id, latency, timestamp)

    @staticmethod
    def checkpoint(
        state: ProductionState, cp: Checkpoint, timestamp: float
    ) -> ProductionState:
        return RuntimeOperations.checkpoint(state, cp, timestamp)

    @staticmethod
    def restore(
        state: ProductionState, checkpoint_id: str, timestamp: float
    ) -> ProductionState:
        return RuntimeOperations.restore(state, checkpoint_id, timestamp)

    @staticmethod
    def recover(
        state: ProductionState, reason: str, timestamp: float
    ) -> ProductionState:
        return RecoveryEngine.recover(state, reason, timestamp)

    @staticmethod
    def health(
        state: ProductionState, c: float, m: float, bl: int, b_up: bool, m_up: bool, ts: float
    ) -> ProductionState:
        return RuntimeOperations.update_health(state, c, m, bl, b_up, m_up, ts)

    @staticmethod
    def log(state: ProductionState, entry: LogEntry) -> ProductionState:
        return RuntimeOperations.log(state, entry)

    @staticmethod
    def register_module(
        state: ProductionState, 
        module_id: str, 
        timestamp: float
    ) -> ProductionState:
        return Supervisor.register_module(state, module_id, timestamp)

    @staticmethod
    def start_module(state: ProductionState, module_id: str, timestamp: float) -> ProductionState:
        return Supervisor.start_module(state, module_id, timestamp)

    @staticmethod
    def stop_module(state: ProductionState, module_id: str, timestamp: float) -> ProductionState:
        return Supervisor.stop_module(state, module_id, timestamp)

    @staticmethod
    def restart_module(state: ProductionState, module_id: str, timestamp: float) -> ProductionState:
        return Supervisor.restart_module(state, module_id, timestamp)