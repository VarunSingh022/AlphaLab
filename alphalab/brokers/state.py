"""Global immutable state container for the Broker Connector Framework."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.brokers.account import AccountSnapshot
from alphalab.brokers.connection import BrokerConnection
from alphalab.brokers.events import BrokerEvent
from alphalab.brokers.execution import ExecutionReport
from alphalab.brokers.order import BrokerOrder
from alphalab.brokers.position import PositionSnapshot


@dataclass(frozen=True, slots=True)
class BrokerStatistics:
    """Immutable tracking metrics for the broker framework."""

    total_orders_submitted: int = 0
    total_executions_received: int = 0
    total_errors: int = 0


@dataclass(frozen=True, slots=True)
class BrokerConnectorState:
    """Deterministic snapshot of multi-broker execution layers."""

    engine_id: str
    connections: Mapping[str, BrokerConnection] = field(default_factory=dict)
    accounts: Mapping[str, AccountSnapshot] = field(default_factory=dict)
    positions: Mapping[str, PositionSnapshot] = field(default_factory=dict)
    orders: Mapping[str, BrokerOrder] = field(default_factory=dict)
    executions: Mapping[str, ExecutionReport] = field(default_factory=dict)
    statistics: BrokerStatistics = field(default_factory=BrokerStatistics)
    events: tuple[BrokerEvent, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)
