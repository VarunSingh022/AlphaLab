"""The canonical broker order.

This is the order as it exists *at a venue*, and it is the type every broker
adapter -- single-broker or multi-broker -- speaks. Before v2.3
``alphalab.brokers.order`` defined a second, near-identical order with a
different identity model; that module now re-exports this one.

Two identifiers, deliberately
-----------------------------

``oms_order_id`` is AlphaLab's order, ``broker_order_id`` is the venue's handle
for it. A single ``order_id`` cannot say which it means, and reconciliation --
matching what AlphaLab believes against what the venue reports -- is exactly the
operation that needs to distinguish them. ``external_id`` on
:class:`~alphalab.broker.execution.BrokerExecution` completes the mapping for
fills.

Status
------

``status`` is the canonical :class:`alphalab.core.enums.OrderStatus` for every
shared lifecycle state. :class:`BrokerOrderStatus` covers only the states that
exist between AlphaLab and the venue and nowhere else: an order staged for
submission, one sent but not yet acknowledged, and a cancel in flight. Those have
no meaning in the OMS, so they stay subsystem-local rather than polluting the
canonical lifecycle -- but there is one set of them, shared by every adapter,
rather than one per package.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from types import MappingProxyType
from typing import Final

from alphalab.core.enums import OrderStatus as CoreOrderStatus
from alphalab.core.enums import OrderType as CoreOrderType
from alphalab.core.enums import Side as CoreSide
from alphalab.core.enums import TimeInForce as CoreTimeInForce
from alphalab.core.lifecycle import TERMINAL_ORDER_STATUSES

__all__ = [
    "BROKER_LOCAL_EQUIVALENTS",
    "BROKER_STATUS_EQUIVALENTS",
    "BrokerOrder",
    "BrokerOrderStatus",
    "canonical_status",
]


class BrokerOrderStatus(Enum):
    """Broker-local operational states that must remain subsystem-specific.

    Only non-canonical, connector/broker workflow values live here. Shared
    lifecycle statuses (accepted, filled, etc.) are represented by
    `alphalab.core.enums.OrderStatus`.
    """

    #: Staged locally; not yet sent to the venue.
    PENDING_SUBMIT = auto()

    #: Sent to the venue; no acknowledgement yet.
    SUBMITTED = auto()

    #: Cancel requested; the venue has not confirmed it.
    PENDING_CANCEL = auto()


#: Which canonical statuses each broker-local status is *consistent with*.
#:
#: A broker-local status describes something in flight between AlphaLab and a
#: venue, so the canonical status the other side holds may be any of several:
#: an order ``SUBMITTED`` and not yet acknowledged is ``NEW``, ``PENDING`` or
#: already ``ACCEPTED`` depending on which message has arrived. Comparing the
#: two vocabularies for equality would report every in-flight order as a break.
#:
#: Stated in v3.5 in :mod:`alphalab.lifecycle.reconciliation`, which compares the
#: OMS book against this mirror, and moved here in v3.9, beside the statuses it
#: relates, because the venue-snapshot reconciliation in
#: :mod:`alphalab.broker.reconciliation` needs the same relation and may not
#: import the lifecycle package. The lifecycle module re-exports this object.
BROKER_STATUS_EQUIVALENTS: Final[Mapping[BrokerOrderStatus, frozenset[CoreOrderStatus]]] = (
    MappingProxyType(
        {
            BrokerOrderStatus.PENDING_SUBMIT: frozenset(
                {CoreOrderStatus.NEW, CoreOrderStatus.PENDING, CoreOrderStatus.ACCEPTED}
            ),
            BrokerOrderStatus.SUBMITTED: frozenset(
                {CoreOrderStatus.NEW, CoreOrderStatus.PENDING, CoreOrderStatus.ACCEPTED}
            ),
            BrokerOrderStatus.PENDING_CANCEL: frozenset(
                {
                    CoreOrderStatus.CANCEL_PENDING,
                    CoreOrderStatus.ACCEPTED,
                    CoreOrderStatus.PARTIALLY_FILLED,
                }
            ),
        }
    )
)

#: The *one* canonical status each broker-local status behaves as, for transitions.
#:
#: ``PENDING_SUBMIT`` is an order not yet sent, which is ``NEW``; ``SUBMITTED``
#: is one sent and not acknowledged, which is ``PENDING``; ``PENDING_CANCEL`` is
#: ``CANCEL_PENDING`` under the name the venue boundary has always used. Where
#: :data:`BROKER_STATUS_EQUIVALENTS` answers "which statuses could the other side
#: hold?", this answers "which row of
#: :data:`~alphalab.core.lifecycle.ORDER_TRANSITIONS` does this order read?" --
#: and each answer here is a member of the set there.
BROKER_LOCAL_EQUIVALENTS: Final[Mapping[BrokerOrderStatus, CoreOrderStatus]] = MappingProxyType(
    {
        BrokerOrderStatus.PENDING_SUBMIT: CoreOrderStatus.NEW,
        BrokerOrderStatus.SUBMITTED: CoreOrderStatus.PENDING,
        BrokerOrderStatus.PENDING_CANCEL: CoreOrderStatus.CANCEL_PENDING,
    }
)


def canonical_status(status: CoreOrderStatus | BrokerOrderStatus) -> CoreOrderStatus:
    """The canonical status an order in ``status`` behaves as."""

    if isinstance(status, BrokerOrderStatus):
        return BROKER_LOCAL_EQUIVALENTS[status]
    return status


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """Immutable representation of an order residing at an external broker.

    Attributes:
        broker_order_id: The venue's handle for this order.
        oms_order_id: The AlphaLab OMS order this represents.
        symbol: Instrument identifier as the venue names it.
        side: Canonical execution direction.
        order_type: Canonical order instruction.
        quantity: Total ordered quantity.
        price: Limit price, or the reference price for a market order.
        filled_quantity: Quantity executed so far.
        average_fill_price: Volume-weighted average of the fills so far.
        status: Canonical lifecycle status, or a broker-local operational one.
        created_at: Unix timestamp the order was created.
        updated_at: Unix timestamp of the most recent transition.
        account_id: Account the order belongs to. Empty when the adapter serves
            a single implicit account.
        tif: Order lifetime policy. Defaults to ``DAY``.
        stop_price: Stop trigger price; ``0`` when the order has no stop.
    """

    broker_order_id: str
    oms_order_id: str
    symbol: str
    side: CoreSide
    order_type: CoreOrderType
    quantity: Decimal
    price: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal
    status: CoreOrderStatus | BrokerOrderStatus
    created_at: float
    updated_at: float

    account_id: str = ""
    tif: CoreTimeInForce = CoreTimeInForce.DAY
    stop_price: Decimal = field(default=Decimal("0"))

    @property
    def remaining_quantity(self) -> Decimal:
        """Quantity still working at the venue."""

        return self.quantity - self.filled_quantity

    @property
    def is_terminal(self) -> bool:
        """Whether the venue can never report another fill for this order.

        A terminal order that receives a fill is a reconciliation failure, not a
        lifecycle transition -- see :mod:`alphalab.broker.reconciliation`. The set
        is :data:`~alphalab.core.lifecycle.TERMINAL_ORDER_STATUSES`, the one the
        OMS reads; no broker-local status is terminal.
        """

        return self.status in TERMINAL_ORDER_STATUSES
