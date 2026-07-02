"""Immutable interface protocol for Broker implementations."""

from decimal import Decimal
from typing import Protocol

from alphalab.broker.events import BrokerEvent
from alphalab.broker.order import BrokerOrder
from alphalab.broker.state import BrokerState


class BrokerProtocol(Protocol):
    """Pure functional interface mapping external broker behaviors."""

    def submit_order(
        self, state: BrokerState, order: BrokerOrder, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        ...

    def cancel_order(
        self, state: BrokerState, broker_order_id: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        ...

    def replace_order(
        self, 
        state: BrokerState, 
        broker_order_id: str, 
        new_quantity: Decimal, 
        new_price: Decimal, 
        timestamp: float,
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        ...

    def heartbeat(
        self, state: BrokerState, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        ...

    def connect(
        self, state: BrokerState, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        ...

    def disconnect(
        self, state: BrokerState, reason: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        ...