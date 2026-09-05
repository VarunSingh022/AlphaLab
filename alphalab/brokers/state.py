"""Global immutable state container for the Broker Connector Framework."""

from dataclasses import dataclass, field

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.brokers.connection import BrokerConnection
from alphalab.brokers.events import BrokerEvent
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap


@dataclass(frozen=True, slots=True)
class BrokerStatistics:
    """Immutable tracking metrics for the broker framework."""

    total_orders_submitted: int = 0
    total_executions_received: int = 0
    total_errors: int = 0


@dataclass(frozen=True, slots=True)
class BrokerConnectorState:
    """Deterministic snapshot of multi-broker execution layers.

    Every domain value here is the canonical one from :mod:`alphalab.broker`;
    what this state adds is *which* broker and account each belongs to.

    The indexes are :class:`~alphalab.common.persistent_map.PersistentMap` and
    ``events`` is an :class:`~alphalab.common.append_log.AppendOnlyLog`, for the
    same reason the OMS switched in v2.2: rebuilding a ``dict`` per order made
    routing cost grow with the square of the orders routed.

    Attributes:
        engine_id: Identifier for this router.
        connections: Registered brokers, keyed by ``broker_id``.
        accounts: Account snapshots, keyed by ``account_id``.
        positions: Positions, keyed by ``"<account_id>:<symbol>"``.
        orders: Orders, keyed by ``broker_order_id``.
        executions: Applied fills, keyed by ``execution_id``.
        statistics: Routing and settlement counters.
        events: Everything that has happened, in order.
        metadata: Router-specific attributes with no canonical field.
    """

    engine_id: str
    connections: PersistentMap[str, BrokerConnection] = field(default_factory=PersistentMap)
    accounts: PersistentMap[str, BrokerAccount] = field(default_factory=PersistentMap)
    positions: PersistentMap[str, BrokerPosition] = field(default_factory=PersistentMap)
    orders: PersistentMap[str, BrokerOrder] = field(default_factory=PersistentMap)
    executions: PersistentMap[str, BrokerExecution] = field(default_factory=PersistentMap)
    statistics: BrokerStatistics = field(default_factory=BrokerStatistics)
    events: AppendOnlyLog[BrokerEvent] = field(default_factory=AppendOnlyLog)
    metadata: PersistentMap[str, str] = field(default_factory=PersistentMap)
