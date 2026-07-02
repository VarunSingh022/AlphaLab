"""Global immutable state container for the Broker Abstraction Layer."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto

from alphalab.broker.account import BrokerAccount
from alphalab.broker.events import BrokerEvent
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition


class ConnectionStatus(Enum):
    """Connection states for the external broker."""
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()


@dataclass(frozen=True, slots=True)
class BrokerState:
    """Deterministic snapshot of broker account, connection, and order state."""
    broker_name: str
    connection_status: ConnectionStatus
    account: BrokerAccount
    positions: Mapping[str, BrokerPosition] = field(default_factory=dict)
    orders: Mapping[str, BrokerOrder] = field(default_factory=dict)
    executions: Mapping[str, BrokerExecution] = field(default_factory=dict)
    events: tuple[BrokerEvent, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)