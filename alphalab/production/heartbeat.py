"""Deterministic tracking models for component liveliness."""

from dataclasses import dataclass
from enum import Enum, auto


class HeartbeatStatus(Enum):
    ALIVE = auto()
    DELAYED = auto()
    DISCONNECTED = auto()
    TIMEOUT = auto()

@dataclass(frozen=True, slots=True)
class HeartbeatRecord:
    """Immutable heartbeat history for a managed module."""
    module_id: str
    status: HeartbeatStatus
    last_ping_time: float
    missed_count: int = 0
    expected_interval: float = 1.0