"""AlphaLab Broker Connector Framework: routing over the canonical boundary.

This package answers "which broker, which account?". The canonical broker
boundary in :mod:`alphalab.broker` answers "what is an order, a fill, an
account, a position?" -- and as of v2.3 this package routes those canonical
types rather than defining a parallel set of its own.

What lives here is what a single-broker adapter has no use for:
:class:`~alphalab.brokers.connection.BrokerConnection` and
:class:`~alphalab.brokers.connection.BrokerType` (a registered endpoint),
:class:`~alphalab.brokers.registry.BrokerRegistry` (registering and connecting
them), and :class:`~alphalab.brokers.state.BrokerConnectorState` (many brokers,
many accounts, one immutable value).

``AccountSnapshot``, ``PositionSnapshot``, ``ExecutionReport``, ``BrokerOrder``,
``OrderStatus`` and ``AssetClass`` remain importable from here and are now the
canonical types under this package's historical names.
"""

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.position import BrokerPosition
from alphalab.brokers.account import AccountSnapshot
from alphalab.brokers.adapter import BrokerAdapter
from alphalab.brokers.connection import BrokerConnection, BrokerType
from alphalab.brokers.engine import BrokerConnectorEngine
from alphalab.brokers.events import (
    BrokerConnected,
    BrokerDisconnected,
    BrokerEvent,
    BrokerRegistered,
    ExecutionReceived,
    Heartbeat,
    OrderCancelled,
    OrderFilled,
    OrderSubmitted,
)
from alphalab.brokers.exceptions import (
    BrokerConnectorError,
    BrokerValidationError,
    InvalidBrokerStateError,
)
from alphalab.brokers.execution import ExecutionReport
from alphalab.brokers.manager import OrderManager
from alphalab.brokers.order import OrderStatus
from alphalab.brokers.position import AssetClass, PositionSnapshot
from alphalab.brokers.protocol import BrokerProtocol
from alphalab.brokers.registry import BrokerRegistry
from alphalab.brokers.state import BrokerConnectorState, BrokerStatistics
from alphalab.brokers.validation import (
    validate_account,
    validate_broker_registration,
    validate_execution,
    validate_order_cancellation,
    validate_order_submission,
)
from alphalab.brokers.views import (
    active_brokers,
    engine_statistics,
    get_account,
    list_executions,
    list_positions,
    open_orders,
)
from alphalab.core.enums import OrderType, TimeInForce
from alphalab.core.enums import Side as OrderSide

__all__ = [
    "AccountSnapshot",
    "AssetClass",
    "BrokerAccount",
    "BrokerAdapter",
    "BrokerConnected",
    "BrokerConnection",
    "BrokerConnectorEngine",
    "BrokerConnectorError",
    "BrokerConnectorState",
    "BrokerDisconnected",
    "BrokerEvent",
    "BrokerExecution",
    "BrokerOrder",
    "BrokerOrderStatus",
    "BrokerPosition",
    "BrokerProtocol",
    "BrokerRegistered",
    "BrokerRegistry",
    "BrokerStatistics",
    "BrokerType",
    "BrokerValidationError",
    "ExecutionReceived",
    "ExecutionReport",
    "Heartbeat",
    "InvalidBrokerStateError",
    "OrderCancelled",
    "OrderFilled",
    "OrderManager",
    "OrderSide",
    "OrderStatus",
    "OrderSubmitted",
    "OrderType",
    "PositionSnapshot",
    "TimeInForce",
    "active_brokers",
    "engine_statistics",
    "get_account",
    "list_executions",
    "list_positions",
    "open_orders",
    "validate_account",
    "validate_broker_registration",
    "validate_execution",
    "validate_order_cancellation",
    "validate_order_submission",
]
