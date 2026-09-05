"""Global immutable state container for the Broker Abstraction Layer."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto

from alphalab.broker.account import BrokerAccount
from alphalab.broker.events import BrokerEvent
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap

__all__ = ["BrokerState", "ConnectionStatus"]


class ConnectionStatus(Enum):
    """Connection states for the external broker.

    ``RECONNECTING`` and ``FAILED`` distinguish the two ways a connection can be
    down, which matters because they call for different behaviour: a
    reconnecting adapter should hold orders, a failed one should refuse them.
    Nothing here reconnects on its own -- these are states an adapter reports,
    not a policy this layer runs.
    """

    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    FAILED = auto()

    @property
    def can_trade(self) -> bool:
        """Whether it is safe to send an order in this state."""

        return self is ConnectionStatus.CONNECTED


@dataclass(frozen=True, slots=True)
class BrokerState:
    """Deterministic snapshot of broker account, connection, and order state.

    The keyed indexes are
    :class:`~alphalab.common.persistent_map.PersistentMap` and ``events`` is an
    :class:`~alphalab.common.append_log.AppendOnlyLog`. As ``dict`` and
    ``tuple`` they were rebuilt on every transition, so a session's cost grew
    with the square of the orders it had placed -- the same quadratic v2.1 and
    v2.2 removed from the risk engine and the OMS. Now that paper and live
    execution run through this state, it would have been the slowest part of a
    long session.

    Attributes:
        broker_name: Identifier of the broker this state describes.
        connection_status: Current connectivity.
        account: Latest account snapshot.
        positions: Open positions, keyed by symbol.
        orders: Orders known to AlphaLab, keyed by ``broker_order_id``.
        executions: Fills applied so far, keyed by ``execution_id``. Membership
            is what makes duplicate-fill rejection possible.
        events: Everything that has happened, in order.
        metadata: Adapter-specific attributes with no canonical field.
        last_heartbeat: Unix timestamp of the most recent heartbeat; ``0.0``
            when none has been received.
    """

    broker_name: str
    connection_status: ConnectionStatus
    account: BrokerAccount
    positions: PersistentMap[str, BrokerPosition] = field(default_factory=PersistentMap)
    orders: PersistentMap[str, BrokerOrder] = field(default_factory=PersistentMap)
    executions: PersistentMap[str, BrokerExecution] = field(default_factory=PersistentMap)
    events: AppendOnlyLog[BrokerEvent] = field(default_factory=AppendOnlyLog)
    metadata: Mapping[str, str] = field(default_factory=dict)
    last_heartbeat: float = 0.0
