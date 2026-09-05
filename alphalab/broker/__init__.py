"""AlphaLab Broker Abstraction Layer: the canonical broker adapter boundary.

This package defines the vocabulary every broker adapter speaks -- one order,
one execution, one account, one position -- and the contract
(:class:`~alphalab.broker.protocol.BrokerProtocol`) an adapter implements to put
a venue behind it. :class:`~alphalab.broker.paper.PaperBroker` is the reference
implementation and a usable paper venue.

:mod:`alphalab.brokers` is the multi-broker router layered above this boundary;
it routes these types rather than defining its own.
"""

from alphalab.broker.account import BrokerAccount
from alphalab.broker.adapter import BrokerAdapter, OMSOrderProtocol
from alphalab.broker.broker import BrokerEngine
from alphalab.broker.events import (
    BrokerConnected,
    BrokerDisconnected,
    BrokerEvent,
    ExecutionReceived,
    Heartbeat,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    OrderSubmitted,
)
from alphalab.broker.exceptions import (
    BrokerError,
    BrokerValidationError,
    InvalidBrokerStateError,
)
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import (
    BrokerOrder,
    BrokerOrderStatus,
)
from alphalab.broker.paper import PaperBroker
from alphalab.broker.position import BrokerPosition
from alphalab.broker.protocol import BrokerProtocol
from alphalab.broker.reconciliation import (
    ExecutionDecision,
    ExecutionOutcome,
    ExternalOrderMap,
    OrderDivergence,
    PositionDivergence,
    ReconciliationLog,
    ReconciliationReport,
    apply_execution,
    classify_execution,
    reconcile,
)
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.broker.validation import (
    validate_cancel_request,
    validate_execution,
    validate_order_submission,
)
from alphalab.broker.views import account_snapshot, executions, open_orders, positions
from alphalab.core.enums import AssetType as BrokerAssetClass
from alphalab.core.enums import OrderType as BrokerOrderType
from alphalab.core.enums import Side as BrokerOrderSide
from alphalab.core.enums import TimeInForce as BrokerTimeInForce

__all__ = [
    "BrokerAccount",
    "BrokerAdapter",
    "BrokerAssetClass",
    "BrokerConnected",
    "BrokerDisconnected",
    "BrokerEngine",
    "BrokerError",
    "BrokerEvent",
    "BrokerExecution",
    "BrokerOrder",
    "BrokerOrderSide",
    "BrokerOrderStatus",
    "BrokerOrderType",
    "BrokerPosition",
    "BrokerProtocol",
    "BrokerState",
    "BrokerTimeInForce",
    "BrokerValidationError",
    "ConnectionStatus",
    "ExecutionDecision",
    "ExecutionOutcome",
    "ExecutionReceived",
    "ExternalOrderMap",
    "Heartbeat",
    "InvalidBrokerStateError",
    "OMSOrderProtocol",
    "OrderAccepted",
    "OrderCancelled",
    "OrderDivergence",
    "OrderRejected",
    "OrderSubmitted",
    "PaperBroker",
    "PositionDivergence",
    "ReconciliationLog",
    "ReconciliationReport",
    "account_snapshot",
    "apply_execution",
    "classify_execution",
    "executions",
    "open_orders",
    "positions",
    "reconcile",
    "validate_cancel_request",
    "validate_execution",
    "validate_order_submission",
]
