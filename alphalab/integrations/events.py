"""Immutable domain events describing the Integration lifecycle."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class IntegrationEvent:
    event_id: str
    timestamp: float

@dataclass(frozen=True, slots=True)
class BrokerConnected(IntegrationEvent):
    broker_id: str

@dataclass(frozen=True, slots=True)
class BrokerDisconnected(IntegrationEvent):
    broker_id: str
    reason: str

@dataclass(frozen=True, slots=True)
class ConnectionRecovered(IntegrationEvent):
    broker_id: str

@dataclass(frozen=True, slots=True)
class AuthenticationSucceeded(IntegrationEvent):
    broker_id: str

@dataclass(frozen=True, slots=True)
class AuthenticationFailed(IntegrationEvent):
    broker_id: str
    reason: str

@dataclass(frozen=True, slots=True)
class OrderSubmitted(IntegrationEvent):
    broker_id: str
    order_id: str

@dataclass(frozen=True, slots=True)
class OrderAccepted(IntegrationEvent):
    broker_id: str
    order_id: str
    remote_order_id: str

@dataclass(frozen=True, slots=True)
class OrderRejected(IntegrationEvent):
    broker_id: str
    order_id: str
    reason: str

@dataclass(frozen=True, slots=True)
class OrderFilled(IntegrationEvent):
    broker_id: str
    order_id: str
    filled_quantity: Decimal
    fill_price: Decimal

@dataclass(frozen=True, slots=True)
class OrderCancelled(IntegrationEvent):
    broker_id: str
    order_id: str

@dataclass(frozen=True, slots=True)
class PortfolioSynchronized(IntegrationEvent):
    broker_id: str
    drift_detected: bool