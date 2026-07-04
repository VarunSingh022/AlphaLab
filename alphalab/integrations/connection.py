"""Immutable connection management models."""

from dataclasses import dataclass
from enum import Enum, auto


class ConnectionStatus(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECOVERING = auto()

@dataclass(frozen=True, slots=True)
class ConnectionState:
    broker_id: str
    status: ConnectionStatus
    last_heartbeat: float = 0.0