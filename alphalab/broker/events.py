"""Immutable domain events describing changes in the Broker Layer."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BrokerEvent:
    """Base class for all Broker lifecycle events."""
    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class BrokerConnected(BrokerEvent):
    """Emitted when a connection to a broker is successfully established."""
    broker_name: str


@dataclass(frozen=True, slots=True)
class BrokerDisconnected(BrokerEvent):
    """Emitted when the connection to a broker is lost."""
    broker_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class OrderSubmitted(BrokerEvent):
    """Emitted when an order is successfully sent to the broker."""
    broker_order_id: str
    oms_order_id: str


@dataclass(frozen=True, slots=True)
class OrderAccepted(BrokerEvent):
    """Emitted when the broker acknowledges receipt and acceptance of an order."""
    broker_order_id: str


@dataclass(frozen=True, slots=True)
class OrderRejected(BrokerEvent):
    """Emitted when the broker rejects an order."""
    broker_order_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class OrderCancelled(BrokerEvent):
    """Emitted when an order is successfully cancelled at the broker."""
    broker_order_id: str


@dataclass(frozen=True, slots=True)
class ExecutionReceived(BrokerEvent):
    """Emitted when an execution (fill) is received from the broker."""
    execution_id: str
    broker_order_id: str
    fill_quantity: Decimal
    fill_price: Decimal


@dataclass(frozen=True, slots=True)
class Heartbeat(BrokerEvent):
    """Emitted periodically to indicate the broker connection is alive."""
    broker_name: str