"""Immutable domain events describing the Broker Connector lifecycle."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BrokerEvent:
    """Base class for all Broker Connector events."""

    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class BrokerRegistered(BrokerEvent):
    broker_id: str
    broker_type: str


@dataclass(frozen=True, slots=True)
class BrokerConnected(BrokerEvent):
    broker_id: str


@dataclass(frozen=True, slots=True)
class BrokerDisconnected(BrokerEvent):
    broker_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class OrderSubmitted(BrokerEvent):
    order_id: str
    account_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class OrderCancelled(BrokerEvent):
    order_id: str
    account_id: str


@dataclass(frozen=True, slots=True)
class OrderFilled(BrokerEvent):
    order_id: str
    account_id: str
    fill_quantity: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionReceived(BrokerEvent):
    execution_id: str
    order_id: str
    fill_price: Decimal
    fill_quantity: Decimal


@dataclass(frozen=True, slots=True)
class Heartbeat(BrokerEvent):
    broker_id: str
    latency_ms: float
