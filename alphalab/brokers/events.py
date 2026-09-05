"""Immutable domain events describing the Broker Connector lifecycle.

Two identifiers appear on these events, and they are not interchangeable:

``broker_order_id``
    The venue's handle for an order -- the same identifier
    :class:`alphalab.broker.order.BrokerOrder` carries under that name, and the
    key :class:`~alphalab.brokers.state.BrokerConnectorState` stores orders
    under. Before v2.3 this field was called ``order_id``, which could not say
    whether it held AlphaLab's identifier or the venue's. The value was always
    the venue handle; only the name was ambiguous. Since telling the two apart
    is precisely what reconciliation does (ADR-0012), the name now says which
    one it is.

``account_id``
    Which account at that broker the order belongs to. This is what makes these
    events *routing* events rather than adapter events: the single-broker
    equivalents in :mod:`alphalab.broker.events` have no account to name.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.common.events import BaseEvent

__all__ = [
    "BrokerConnected",
    "BrokerDisconnected",
    "BrokerEvent",
    "BrokerRegistered",
    "ExecutionReceived",
    "Heartbeat",
    "OrderCancelled",
    "OrderFilled",
    "OrderSubmitted",
]


@dataclass(frozen=True, slots=True)
class BrokerEvent(BaseEvent):
    """Base class for all Broker Connector events."""

    pass


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
    broker_order_id: str
    account_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class OrderCancelled(BrokerEvent):
    broker_order_id: str
    account_id: str


@dataclass(frozen=True, slots=True)
class OrderFilled(BrokerEvent):
    broker_order_id: str
    account_id: str
    fill_quantity: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionReceived(BrokerEvent):
    execution_id: str
    broker_order_id: str
    fill_price: Decimal
    fill_quantity: Decimal


@dataclass(frozen=True, slots=True)
class Heartbeat(BrokerEvent):
    broker_id: str
    latency_ms: float
