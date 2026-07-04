"""Immutable domain events describing the Production Runtime lifecycle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProductionEvent:
    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class RuntimeStarted(ProductionEvent):
    runtime_id: str


@dataclass(frozen=True, slots=True)
class RuntimeStopped(ProductionEvent):
    runtime_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class HeartbeatGenerated(ProductionEvent):
    module_id: str
    latency_ms: float


@dataclass(frozen=True, slots=True)
class HeartbeatTimeout(ProductionEvent):
    module_id: str
    missed_beats: int


@dataclass(frozen=True, slots=True)
class HealthUpdated(ProductionEvent):
    health_score: float


@dataclass(frozen=True, slots=True)
class CheckpointCreated(ProductionEvent):
    checkpoint_id: str


@dataclass(frozen=True, slots=True)
class CheckpointRestored(ProductionEvent):
    checkpoint_id: str


@dataclass(frozen=True, slots=True)
class RecoveryStarted(ProductionEvent):
    reason: str


@dataclass(frozen=True, slots=True)
class RecoveryCompleted(ProductionEvent):
    restored_checkpoint_id: str


@dataclass(frozen=True, slots=True)
class ModuleStarted(ProductionEvent):
    module_id: str


@dataclass(frozen=True, slots=True)
class ModuleStopped(ProductionEvent):
    module_id: str


@dataclass(frozen=True, slots=True)
class ModuleRestarted(ProductionEvent):
    module_id: str
    attempt: int


@dataclass(frozen=True, slots=True)
class AlertRaised(ProductionEvent):
    alert_id: str
    severity: str
    message: str
