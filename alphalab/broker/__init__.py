"""AlphaLab Broker Abstraction Layer: the canonical broker adapter boundary.

This package defines the vocabulary every broker adapter speaks -- one order,
one execution, one account, one position -- and the contract
(:class:`~alphalab.broker.protocol.BrokerProtocol`) an adapter implements to put
a venue behind it. :class:`~alphalab.broker.paper.PaperBroker` is the reference
implementation and a usable paper venue.

Since v3.9 the vocabulary is the whole universal execution contract: what a
venue declares it can do (the capability model, defined in
:mod:`alphalab.core.capabilities` and re-exported here as the same objects),
what it reports (:class:`~alphalab.broker.lifecycle.VenueEvent`, applied by
:func:`~alphalab.broker.lifecycle.apply_venue_event`), what AlphaLab asks of it
beyond submission (:mod:`alphalab.broker.requests`), and how the two sides are
compared (:func:`~alphalab.broker.reconciliation.reconcile_snapshot`). An
application's adapter translates this vocabulary to a vendor's protocol; nothing
in this package names or reaches a vendor.

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
from alphalab.broker.lifecycle import (
    LifecycleDecision,
    LifecycleOutcome,
    VenueEvent,
    apply_venue_event,
    apply_venue_events,
    classify_venue_event,
)
from alphalab.broker.order import (
    BROKER_LOCAL_EQUIVALENTS,
    BROKER_STATUS_EQUIVALENTS,
    BrokerOrder,
    BrokerOrderStatus,
    canonical_status,
)
from alphalab.broker.paper import PaperBroker
from alphalab.broker.position import BrokerPosition
from alphalab.broker.protocol import BrokerProtocol
from alphalab.broker.reconciliation import (
    SNAPSHOT_RECONCILIATION_SCHEME,
    VENUE_SNAPSHOT_SCHEME,
    ExecutionDecision,
    ExecutionOutcome,
    ExternalOrderMap,
    OrderDivergence,
    PositionDivergence,
    ReconciliationLog,
    ReconciliationReport,
    SnapshotDivergence,
    SnapshotDivergenceKind,
    SnapshotFreshness,
    SnapshotReconciliation,
    VenueSnapshot,
    apply_execution,
    classify_execution,
    reconcile,
    reconcile_snapshot,
)
from alphalab.broker.requests import (
    VENUE_REQUEST_SCHEME,
    CancelRequest,
    ModifyRequest,
    RequestDecision,
    RequestLedger,
    RequestOutcome,
    issue_cancel,
    issue_modify,
)
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.broker.validation import (
    validate_cancel_request,
    validate_execution,
    validate_order_submission,
    validate_replace_request,
)
from alphalab.broker.views import account_snapshot, executions, open_orders, positions
from alphalab.core.capabilities import (
    ANY_LISTING_VENUE,
    AccountCapability,
    Capability,
    CapabilityCheck,
    CapabilityDeclaration,
    CapabilityLevel,
    Compatibility,
    CompatibilityReport,
    ExecutionRequirements,
    MarketCapability,
    RequirementDimension,
    Support,
    check_compatibility,
    order_requirements,
    supports,
)
from alphalab.core.enums import AssetType as BrokerAssetClass
from alphalab.core.enums import OrderType as BrokerOrderType
from alphalab.core.enums import Side as BrokerOrderSide
from alphalab.core.enums import TimeInForce as BrokerTimeInForce
from alphalab.core.lifecycle import ExecutionEventKind

__all__ = [
    "ANY_LISTING_VENUE",
    "BROKER_LOCAL_EQUIVALENTS",
    "BROKER_STATUS_EQUIVALENTS",
    "SNAPSHOT_RECONCILIATION_SCHEME",
    "VENUE_REQUEST_SCHEME",
    "VENUE_SNAPSHOT_SCHEME",
    "AccountCapability",
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
    "CancelRequest",
    "Capability",
    "CapabilityCheck",
    "CapabilityDeclaration",
    "CapabilityLevel",
    "Compatibility",
    "CompatibilityReport",
    "ConnectionStatus",
    "ExecutionDecision",
    "ExecutionEventKind",
    "ExecutionOutcome",
    "ExecutionReceived",
    "ExecutionRequirements",
    "ExternalOrderMap",
    "Heartbeat",
    "InvalidBrokerStateError",
    "LifecycleDecision",
    "LifecycleOutcome",
    "MarketCapability",
    "ModifyRequest",
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
    "RequestDecision",
    "RequestLedger",
    "RequestOutcome",
    "RequirementDimension",
    "SnapshotDivergence",
    "SnapshotDivergenceKind",
    "SnapshotFreshness",
    "SnapshotReconciliation",
    "Support",
    "VenueEvent",
    "VenueSnapshot",
    "account_snapshot",
    "apply_execution",
    "apply_venue_event",
    "apply_venue_events",
    "canonical_status",
    "check_compatibility",
    "classify_execution",
    "classify_venue_event",
    "executions",
    "issue_cancel",
    "issue_modify",
    "open_orders",
    "order_requirements",
    "positions",
    "reconcile",
    "reconcile_snapshot",
    "supports",
    "validate_cancel_request",
    "validate_execution",
    "validate_order_submission",
    "validate_replace_request",
]
