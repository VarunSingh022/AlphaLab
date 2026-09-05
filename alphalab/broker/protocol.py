"""The canonical broker adapter contract.

This is the boundary between AlphaLab and a venue. Above it, everything is
AlphaLab's domain model. Below it, an adapter may do whatever a venue's API
requires -- HTTP, FIX, a socket, a vendor SDK -- and none of that is visible
here on purpose.

The contract in one line: every method takes the current
:class:`~alphalab.broker.state.BrokerState` and returns the next one plus the
events it produced. An adapter holds no mutable state of its own, which is what
lets the same adapter be driven by a backtest, a paper run and a live session
without behaving differently.

What an adapter is asked for
----------------------------

======================== ==================================================
Order flow               ``submit_order``, ``cancel_order``, ``replace_order``
Order state              ``order_status``
Execution reception      ``apply_execution``
Account information      ``account``
Positions                ``positions``
Connectivity             ``connect``, ``disconnect``, ``heartbeat``, ``status``
======================== ==================================================

Reconciliation is deliberately *not* an adapter method. It compares what
AlphaLab believes against what a venue reports, so it is a function over two
snapshots rather than a call an adapter answers --- see
:mod:`alphalab.broker.reconciliation`.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol, runtime_checkable

from alphalab.broker.account import BrokerAccount
from alphalab.broker.events import BrokerEvent
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.broker.state import BrokerState, ConnectionStatus

__all__ = ["BrokerProtocol"]


@runtime_checkable
class BrokerProtocol(Protocol):
    """Pure functional interface mapping external broker behaviors."""

    def submit_order(
        self, state: BrokerState, order: BrokerOrder, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Send an order to the venue."""
        ...

    def cancel_order(
        self, state: BrokerState, broker_order_id: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Ask the venue to cancel a working order."""
        ...

    def replace_order(
        self,
        state: BrokerState,
        broker_order_id: str,
        new_quantity: Decimal,
        new_price: Decimal,
        timestamp: float,
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Amend a working order's quantity or price."""
        ...

    def apply_execution(
        self, state: BrokerState, execution: BrokerExecution, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Apply a fill the venue reported.

        Implementations must be idempotent in ``execution.execution_id``: a
        venue may redeliver a fill after a reconnect, and applying it twice
        would double-count it.
        """
        ...

    def order_status(self, state: BrokerState, broker_order_id: str) -> BrokerOrder | None:
        """The order as AlphaLab currently believes the venue holds it."""
        ...

    def account(self, state: BrokerState) -> BrokerAccount:
        """Latest account snapshot."""
        ...

    def positions(self, state: BrokerState) -> Sequence[BrokerPosition]:
        """Open positions at the venue."""
        ...

    def heartbeat(
        self, state: BrokerState, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Record a keep-alive from the venue."""
        ...

    def connect(
        self, state: BrokerState, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Establish the venue connection."""
        ...

    def disconnect(
        self, state: BrokerState, reason: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Tear down the venue connection."""
        ...

    def status(self, state: BrokerState) -> ConnectionStatus:
        """Current connectivity."""
        ...
