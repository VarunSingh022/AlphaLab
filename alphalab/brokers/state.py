"""Global immutable state container for the Broker Connector Framework."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.brokers.connection import BrokerConnection
from alphalab.brokers.events import BrokerEvent


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
    connections: Mapping[str, BrokerConnection] = field(default_factory=dict)
    accounts: Mapping[str, BrokerAccount] = field(default_factory=dict)
    positions: Mapping[str, BrokerPosition] = field(default_factory=dict)
    orders: Mapping[str, BrokerOrder] = field(default_factory=dict)
    executions: Mapping[str, BrokerExecution] = field(default_factory=dict)
    statistics: BrokerStatistics = field(default_factory=BrokerStatistics)
    events: tuple[BrokerEvent, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)
