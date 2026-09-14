"""The connector contract: one adapter, many brokers and many accounts.

This is **not** the canonical venue boundary. That is
:class:`alphalab.broker.protocol.BrokerProtocol`, which every method of takes a
:class:`~alphalab.broker.state.BrokerState` -- *one* broker -- and which
:mod:`alphalab.runtime.broker_routing`, :class:`~alphalab.runtime.live.LiveSession`,
:class:`~alphalab.broker.venue.RestVenueBroker` and
:class:`~alphalab.broker.paper.PaperBroker` all speak.

This protocol sits one layer out. Every method takes a
:class:`~alphalab.brokers.state.BrokerConnectorState`, which holds many brokers
and many accounts, which is why its queries take an ``account_id`` that a
single-venue boundary has no need of. The overlap in verb names -- both connect,
both submit orders -- is what a routing layer over a boundary looks like, and
collapsing the two would force a single-venue adapter to carry a registry it
does not have.

Why the name changed in v2.17
-----------------------------

Until v2.17 this was also called ``BrokerProtocol``, so the repository had two
public symbols of that name standing for two different contracts. ADR-0032
classified it as category C -- "a rename would be a breaking change to a public
symbol for an ergonomic gain" -- and ADR-0034 takes the change, because v2.17 is
the last release before the API freezes and a frozen public API with two
meanings for one name is a defect that cannot be fixed later without breaking
someone.

The new name was not invented for the occasion: this package already spells the
concept out in :class:`~alphalab.brokers.state.BrokerConnectorState`,
:class:`~alphalab.brokers.engine.BrokerConnectorEngine` and
:class:`~alphalab.brokers.exceptions.BrokerConnectorError`. The protocol was the
one member of that family not using the family's word.

**There is no alias.** ``alphalab.brokers.BrokerProtocol`` is gone rather than
redirected: an alias would leave one name meaning two things at the import site,
which is the whole defect being removed. A caller updates the import, and
``ImportError`` says so immediately rather than a type check failing somewhere
downstream.
"""

from typing import Protocol

from alphalab.broker.account import BrokerAccount
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.brokers.events import BrokerEvent
from alphalab.brokers.state import BrokerConnectorState

__all__ = ["BrokerConnectorProtocol"]


class BrokerConnectorProtocol(Protocol):
    """Pure functional interface defining generic multi-broker interaction."""

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
