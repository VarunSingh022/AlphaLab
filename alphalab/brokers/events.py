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
    "BrokerConnectorEvent",
    "BrokerRegistered",
    "RegisteredBrokerConnected",
    "RegisteredBrokerDisconnected",
    "RegisteredBrokerHeartbeat",
    "RoutedExecutionReceived",
    "RoutedOrderCancelled",
    "RoutedOrderFilled",
    "RoutedOrderSubmitted",
]


@dataclass(frozen=True, slots=True)
class BrokerConnectorEvent(BaseEvent):
    """Base class for all Broker Connector events."""

    pass


@dataclass(frozen=True, slots=True)
class BrokerRegistered(BrokerConnectorEvent):
    broker_id: str
    broker_type: str


@dataclass(frozen=True, slots=True)
class RegisteredBrokerConnected(BrokerConnectorEvent):
    broker_id: str


@dataclass(frozen=True, slots=True)
class RegisteredBrokerDisconnected(BrokerConnectorEvent):
    broker_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RoutedOrderSubmitted(BrokerConnectorEvent):
    broker_order_id: str
    account_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class RoutedOrderCancelled(BrokerConnectorEvent):
    broker_order_id: str
    account_id: str


@dataclass(frozen=True, slots=True)
class RoutedOrderFilled(BrokerConnectorEvent):
    broker_order_id: str
    account_id: str
    fill_quantity: Decimal


@dataclass(frozen=True, slots=True)
class RoutedExecutionReceived(BrokerConnectorEvent):
    execution_id: str
    broker_order_id: str
    fill_price: Decimal
    fill_quantity: Decimal


@dataclass(frozen=True, slots=True)
class RegisteredBrokerHeartbeat(BrokerConnectorEvent):
    broker_id: str
    latency_ms: float
