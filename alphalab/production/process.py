"""Immutable models defining supervised system processes."""

from dataclasses import dataclass
from enum import Enum, auto


class ProcessState(Enum):
    STARTING = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()
    FAILED = auto()

@dataclass(frozen=True, slots=True)
class ManagedProcess:
    """Immutable representation of a tracked subsystem module."""
    module_id: str
    state: ProcessState
    restart_count: int = 0
    last_state_change: float = 0.0