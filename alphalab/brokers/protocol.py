"""Immutable interface protocol for Broker implementations."""

from typing import Protocol

from alphalab.broker.account import BrokerAccount
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.brokers.events import BrokerEvent
from alphalab.brokers.state import BrokerConnectorState


class BrokerProtocol(Protocol):
    """Pure functional interface defining generic provider interaction."""

    def connect(
        self, state: BrokerConnectorState, timestamp: float
    ) -> tuple[BrokerConnectorState, tuple[BrokerEvent, ...]]: ...

    def disconnect(
        self, state: BrokerConnectorState, reason: str, timestamp: float
    ) -> tuple[BrokerConnectorState, tuple[BrokerEvent, ...]]: ...

    def submit_order(
        self, state: BrokerConnectorState, order: BrokerOrder, timestamp: float
    ) -> tuple[BrokerConnectorState, tuple[BrokerEvent, ...]]: ...

    def cancel_order(
        self, state: BrokerConnectorState, broker_order_id: str, timestamp: float
    ) -> tuple[BrokerConnectorState, tuple[BrokerEvent, ...]]: ...

    def replace_order(
        self, state: BrokerConnectorState, order: BrokerOrder, timestamp: float
    ) -> tuple[BrokerConnectorState, tuple[BrokerEvent, ...]]: ...

    def query_account(self, state: BrokerConnectorState, account_id: str) -> BrokerAccount: ...

    def query_positions(
        self, state: BrokerConnectorState, account_id: str
    ) -> tuple[BrokerPosition, ...]: ...

    def heartbeat(
        self, state: BrokerConnectorState, latency_ms: float, timestamp: float
    ) -> tuple[BrokerConnectorState, tuple[BrokerEvent, ...]]: ...
