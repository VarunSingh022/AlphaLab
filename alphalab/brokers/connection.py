"""Immutable models defining broker connections."""

from dataclasses import dataclass
from enum import Enum, auto


class BrokerType(Enum):
    """Types of underlying broker communication infrastructures."""

    PAPER = auto()
    REST = auto()
    STREAMING = auto()
    HYBRID = auto()


@dataclass(frozen=True, slots=True)
class BrokerConnection:
    """Immutable snapshot of a broker integration endpoint."""

    broker_id: str
    broker_name: str
    broker_type: BrokerType
    connected: bool = False
    latency_ms: float = 0.0
    last_heartbeat: float = 0.0
