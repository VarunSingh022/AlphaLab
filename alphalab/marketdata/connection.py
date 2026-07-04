"""Immutable tracking models for provider connections."""

from dataclasses import dataclass
from enum import Enum, auto


class ConnectionStatus(Enum):
    DISCONNECTED = auto()
    CONNECTED = auto()
    RECOVERING = auto()

@dataclass(frozen=True, slots=True)
class ConnectionState:
    provider_id: str
    status: ConnectionStatus
    latency_ms: float = 0.0
    last_heartbeat: float = 0.0
    reconnects: int = 0