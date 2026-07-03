"""Immutable domain events describing the Runtime lifecycle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Base class for all Runtime system events."""
    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class RuntimeStarted(RuntimeEvent):
    runtime_id: str


@dataclass(frozen=True, slots=True)
class RuntimeStopped(RuntimeEvent):
    runtime_id: str


@dataclass(frozen=True, slots=True)
class RuntimePaused(RuntimeEvent):
    runtime_id: str


@dataclass(frozen=True, slots=True)
class RuntimeResumed(RuntimeEvent):
    runtime_id: str


@dataclass(frozen=True, slots=True)
class RuntimeFailed(RuntimeEvent):
    runtime_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class Heartbeat(RuntimeEvent):
    runtime_id: str


@dataclass(frozen=True, slots=True)
class DispatchCompleted(RuntimeEvent):
    event_type: str
    processing_time: float


@dataclass(frozen=True, slots=True)
class DispatchFailed(RuntimeEvent):
    event_type: str
    reason: str