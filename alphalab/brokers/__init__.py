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

Since v2.3 the canonical types were also importable from here under this
package's historical names -- ``AccountSnapshot``, ``PositionSnapshot``,
``ExecutionReport``, ``OrderStatus``, ``AssetClass`` -- and since v3.13 they are
not (ledger API-001): ``brokers.ExecutionReport`` was ``BrokerExecution`` while
``execution.ExecutionReport`` is the fill report, and ``brokers.OrderStatus`` was
``BrokerOrderStatus`` while ``core.OrderStatus`` is the lifecycle. One name, one
thing. The connector's own vocabulary is named for what it does -- a
``BrokerConnector...`` type, a ``RegisteredBroker...`` connection event, a
``Routed...`` order event -- where it once reused the boundary's names for
different classes.
"""

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.position import BrokerPosition
from alphalab.brokers.adapter import BrokerConnectorAdapter
from alphalab.brokers.connection import BrokerConnection, BrokerType
from alphalab.brokers.engine import BrokerConnectorEngine
from alphalab.brokers.events import (
    BrokerConnectorEvent,
    BrokerRegistered,
    RegisteredBrokerConnected,
    RegisteredBrokerDisconnected,
    RegisteredBrokerHeartbeat,
    RoutedExecutionReceived,
    RoutedOrderCancelled,
    RoutedOrderFilled,
    RoutedOrderSubmitted,
)
from alphalab.brokers.exceptions import (
    BrokerConnectorError,
    BrokerConnectorStateError,
    BrokerConnectorValidationError,
)
from alphalab.brokers.manager import OrderManager
from alphalab.brokers.protocol import BrokerConnectorProtocol
from alphalab.brokers.registry import BrokerRegistry
from alphalab.brokers.state import BrokerConnectorState, BrokerStatistics
from alphalab.brokers.validation import (
    validate_account,
    validate_broker_registration,
    validate_order_cancellation,
    validate_routed_execution,
    validate_routed_submission,
)
from alphalab.brokers.views import (
    active_brokers,
    engine_statistics,
    get_account,
    list_executions,
    list_positions,
    open_routed_orders,
)
from alphalab.core.enums import OrderType, TimeInForce
from alphalab.core.enums import Side as OrderSide

__all__ = [
    "BrokerAccount",
    "BrokerConnection",
    "BrokerConnectorAdapter",
    "BrokerConnectorEngine",
    "BrokerConnectorError",
    "BrokerConnectorEvent",
    "BrokerConnectorProtocol",
    "BrokerConnectorState",
    "BrokerConnectorStateError",
    "BrokerConnectorValidationError",
    "BrokerExecution",
    "BrokerOrder",
    "BrokerOrderStatus",
    "BrokerPosition",
    "BrokerRegistered",
    "BrokerRegistry",
    "BrokerStatistics",
    "BrokerType",
    "OrderManager",
    "OrderSide",
    "OrderType",
    "RegisteredBrokerConnected",
    "RegisteredBrokerDisconnected",
    "RegisteredBrokerHeartbeat",
    "RoutedExecutionReceived",
    "RoutedOrderCancelled",
    "RoutedOrderFilled",
    "RoutedOrderSubmitted",
    "TimeInForce",
    "active_brokers",
    "engine_statistics",
    "get_account",
    "list_executions",
    "list_positions",
    "open_routed_orders",
    "validate_account",
    "validate_broker_registration",
    "validate_order_cancellation",
    "validate_routed_execution",
    "validate_routed_submission",
]
