"""Cancel and modify requests: telling a retry from a new request.

Submission has been idempotent since v2.3. The client handle is *derived* from
the OMS order id (:func:`~alphalab.runtime.broker_routing.broker_order_id_for`),
so a submission retried after a lost response addresses the order the first
attempt may have created, and
:class:`~alphalab.broker.reconciliation.ExternalOrderMap` makes the retry
observable -- ``route_order`` refuses it as ``DUPLICATE_SUBMISSION``.

Cancelling and amending had no such thing. :class:`~alphalab.broker.protocol.BrokerProtocol`
takes ``cancel_order(state, broker_order_id, timestamp)`` and
``replace_order(state, broker_order_id, new_quantity, new_price, timestamp)``:
an operation with no identity, so an adapter retrying one after a timeout could
not say whether it was repeating a request or making a second one, and neither
could anything auditing it afterwards.

Identity, and what distinguishes a retry
----------------------------------------

A request is identified by its **content and its place in a sequence**, never by
when it was sent -- a retry is sent later and is the same request:

* :class:`CancelRequest` -- the order and an ``attempt`` number. One cancel is
  outstanding per order at a time; a refused cancel
  (``ORDER_CANCEL_REJECTED``) leaves the order working, and cancelling it again
  is a *new* request, so the caller increments ``attempt``.
* :class:`ModifyRequest` -- the order, a ``revision`` number, and the quantity
  and price asked for. Amending to 101, then to 102, then back to 101 is three
  requests; the third has the first one's values and a different revision.

The numbers are the caller's, like a FIX ``ClOrdID`` chain: a retry reuses one
and a new request increments it. :attr:`CancelRequest.request_id` and
:attr:`ModifyRequest.request_id` are SHA-256 digests tagged
:data:`VENUE_REQUEST_SCHEME`, so the same request identifies itself the same way
in every process, and nothing random or clock-derived enters it. An amendment's
quantity and price enter by value
(:func:`~alphalab.common.arithmetic.canonical_text`): until v3.11 they entered as
spelled, so a retry of an amendment to ``100`` spelled ``100.0`` was a different
request with a reused revision, and was refused (ledger DET-006).

The ledger
----------

:class:`RequestLedger` records every request issued, and :func:`issue_cancel` /
:func:`issue_modify` consult it before anything is sent:

=============== ================================================================
``NEW``         Not issued before, and the order can take it. Send it.
``DUPLICATE``   Issued before -- a retry -- or, for a cancel, one is already in
                flight. Do not send a second; the first one's answer is due.
``REFUSED``     Cannot be sent: the order is unknown or finished, the amendment
                changes nothing or leaves nothing working, or the number reuses
                or predates one already issued.
=============== ================================================================

A new cancel moves the mirror order to ``PENDING_CANCEL``, the broker-local
status that has meant "cancel requested; the venue has not confirmed it" since
v2.3. A new amendment changes nothing in the mirror: the venue's
``ORDER_REPLACED`` event does, when it confirms one
(:mod:`alphalab.broker.lifecycle`).

The ledger is a value, like ``ExternalOrderMap``. It is not part of the broker
snapshot, because it is AlphaLab's record of what it asked rather than the
venue's state; a live run holds it on
:attr:`~alphalab.runtime.live.LiveRunState.requests` and, since v3.11, persists
it with the live envelope (:mod:`alphalab.runtime.live_snapshot`), so a
restarted run recognises a retry exactly. A ledger that *is* lost -- a caller
driving this module without the live session -- fails in the safe direction: a
cancel or an amendment is absolute at a venue, so re-sending one cannot trade
twice, and a mirror still ``PENDING_CANCEL`` answers ``DUPLICATE`` for a cancel
on its own evidence.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum, unique
from typing import Final

from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.order import BrokerOrderStatus, canonical_status
from alphalab.broker.state import BrokerState
from alphalab.common.arithmetic import canonical_text
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import OrderStatus

__all__ = [
    "VENUE_REQUEST_SCHEME",
    "CancelRequest",
    "ModifyRequest",
    "RequestDecision",
    "RequestLedger",
    "RequestOutcome",
    "issue_cancel",
    "issue_modify",
]

#: Scheme tag of a request's identity. Changing it is an ADR. Version 2 (v3.11)
#: renders an amendment's quantity and price by value.
VENUE_REQUEST_SCHEME: Final = "alphalab.venue_request.v2"


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _require_instant(value: float, field_name: str) -> None:
    if not math.isfinite(value):
        raise BrokerValidationError(f"{field_name} is {value!r}, which is not an instant.")


@dataclass(frozen=True, slots=True)
class CancelRequest:
    """AlphaLab asking a venue to cancel one working order.

    Attributes:
        broker_order_id: The venue handle.
        attempt: Which cancel of this order this is, from 1. A retry reuses it;
            a cancel sent after the venue refused an earlier one increments it.
        requested_at: When AlphaLab asked. Recorded, and not part of the identity.

    Raises:
        BrokerValidationError: If the handle is blank, the attempt is below 1 or
            the instant is not finite.
    """

    broker_order_id: str
    attempt: int
    requested_at: float

    def __post_init__(self) -> None:
        if not self.broker_order_id:
            raise BrokerValidationError("A cancel request names no order.")
        if self.attempt < 1:
            raise BrokerValidationError(f"Cancel attempts count from 1, got {self.attempt}.")
        _require_instant(self.requested_at, "CancelRequest.requested_at")

    @property
    def request_id(self) -> str:
        """The request's identity: the order and the attempt, never the time."""

        return _digest(
            [
                VENUE_REQUEST_SCHEME,
                "kind=cancel",
                f"order={self.broker_order_id!r}",
                f"attempt={self.attempt}",
            ]
        )


@dataclass(frozen=True, slots=True)
class ModifyRequest:
    """AlphaLab asking a venue to amend one working order's quantity and price.

    Attributes:
        broker_order_id: The venue handle.
        revision: Which amendment of this order this is, from 1. A retry reuses
            it; a new amendment increments it.
        quantity: The order's new *total* quantity, filled quantity included --
            what :meth:`~alphalab.broker.protocol.BrokerProtocol.replace_order`
            takes.
        price: The order's new price.
        requested_at: When AlphaLab asked. Recorded, and not part of the identity.

    Raises:
        BrokerValidationError: If the handle is blank, the revision is below 1,
            the quantity is not positive, the price is negative or the instant is
            not finite.
    """

    broker_order_id: str
    revision: int
    quantity: Decimal
    price: Decimal
    requested_at: float

    def __post_init__(self) -> None:
        if not self.broker_order_id:
            raise BrokerValidationError("A modify request names no order.")
        if self.revision < 1:
            raise BrokerValidationError(f"Revisions count from 1, got {self.revision}.")
        if self.quantity <= 0:
            raise BrokerValidationError(f"An order amended to {self.quantity} is not an order.")
        if self.price < 0:
            raise BrokerValidationError(f"An order amended to price {self.price} is not an order.")
        _require_instant(self.requested_at, "ModifyRequest.requested_at")

    @property
    def request_id(self) -> str:
        """The request's identity: the order, the revision and the values asked for."""

        return _digest(
            [
                VENUE_REQUEST_SCHEME,
                "kind=modify",
                f"order={self.broker_order_id!r}",
                f"revision={self.revision}",
                f"quantity={canonical_text(self.quantity)}",
                f"price={canonical_text(self.price)}",
            ]
        )


@unique
class RequestOutcome(StrEnum):
    """What issuing a request decided. See the module docstring."""

    NEW = "new"
    DUPLICATE = "duplicate"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class RequestDecision:
    """The decision about one request, and why."""

    request: CancelRequest | ModifyRequest
    outcome: RequestOutcome
    reason: str

    @property
    def send(self) -> bool:
        """Whether the request should go to the venue. Only a ``NEW`` one should."""

        return self.outcome is RequestOutcome.NEW


@dataclass(frozen=True, slots=True)
class RequestLedger:
    """Every cancel and modify request issued, by identity, and the latest per order.

    Attributes:
        issued: Every request issued, keyed by ``request_id``.
        latest_cancel: Per order, the highest cancel attempt issued and its id.
        latest_revision: Per order, the highest revision issued and its id.
    """

    issued: PersistentMap[str, CancelRequest | ModifyRequest] = field(default_factory=PersistentMap)
    latest_cancel: PersistentMap[str, tuple[int, str]] = field(default_factory=PersistentMap)
    latest_revision: PersistentMap[str, tuple[int, str]] = field(default_factory=PersistentMap)


def _decided(
    request: CancelRequest | ModifyRequest, outcome: RequestOutcome, reason: str
) -> RequestDecision:
    return RequestDecision(request, outcome, reason)


def _sequenced(number: int, latest: tuple[int, str] | None, request_id: str, noun: str) -> str:
    """Why a sequence number cannot be issued, or ``""`` when it can."""

    if latest is None:
        return ""
    highest, issued_id = latest
    if number == highest and request_id != issued_id:
        return f"{noun} {number} was already issued for this order with different content."
    if number < highest:
        return f"{noun} {number} predates {noun} {highest}, which was already issued."
    return ""


def issue_cancel(
    state: BrokerState, ledger: RequestLedger, request: CancelRequest
) -> tuple[BrokerState, RequestLedger, RequestDecision]:
    """Decide whether a cancel may be sent, and record it if it may.

    A ``NEW`` cancel moves the mirror order to ``PENDING_CANCEL``; every other
    outcome returns both ``state`` and ``ledger`` unchanged.
    """

    request_id = request.request_id
    if request_id in ledger.issued:
        return (
            state,
            ledger,
            _decided(
                request,
                RequestOutcome.DUPLICATE,
                "This cancel was already issued; its answer is due.",
            ),
        )
    order = state.orders.get(request.broker_order_id)
    if order is None:
        return (
            state,
            ledger,
            _decided(
                request,
                RequestOutcome.REFUSED,
                f"Order {request.broker_order_id!r} is not held by the mirror; nothing to cancel.",
            ),
        )
    if order.is_terminal:
        return (
            state,
            ledger,
            _decided(
                request,
                RequestOutcome.REFUSED,
                f"Order {order.broker_order_id} is {order.status}; nothing is left to cancel.",
            ),
        )
    stale = _sequenced(
        request.attempt, ledger.latest_cancel.get(order.broker_order_id), request_id, "Attempt"
    )
    if stale:
        return state, ledger, _decided(request, RequestOutcome.REFUSED, stale)
    if canonical_status(order.status) is OrderStatus.CANCEL_PENDING:
        return (
            state,
            ledger,
            _decided(
                request,
                RequestOutcome.DUPLICATE,
                f"A cancel of order {order.broker_order_id} is already in flight.",
            ),
        )

    pending = replace(
        order,
        status=BrokerOrderStatus.PENDING_CANCEL,
        updated_at=max(order.updated_at, request.requested_at),
    )
    return (
        replace(state, orders=state.orders.set(order.broker_order_id, pending)),
        replace(
            ledger,
            issued=ledger.issued.set(request_id, request),
            latest_cancel=ledger.latest_cancel.set(
                order.broker_order_id, (request.attempt, request_id)
            ),
        ),
        _decided(request, RequestOutcome.NEW, ""),
    )


def issue_modify(
    state: BrokerState, ledger: RequestLedger, request: ModifyRequest
) -> tuple[BrokerState, RequestLedger, RequestDecision]:
    """Decide whether an amendment may be sent, and record it if it may.

    The mirror is never changed here: the amendment exists when the venue
    confirms it. Whether the venue *can* amend in place is a capability --
    ``CANCEL_REPLACE`` in :mod:`alphalab.core.capabilities` -- and is checked by
    whoever chooses between amending and cancelling-and-resending.
    """

    request_id = request.request_id
    if request_id in ledger.issued:
        return (
            state,
            ledger,
            _decided(
                request,
                RequestOutcome.DUPLICATE,
                "This amendment was already issued; its answer is due.",
            ),
        )
    order = state.orders.get(request.broker_order_id)
    if order is None:
        return (
            state,
            ledger,
            _decided(
                request,
                RequestOutcome.REFUSED,
                f"Order {request.broker_order_id!r} is not held by the mirror; nothing to amend.",
            ),
        )
    stale = _sequenced(
        request.revision, ledger.latest_revision.get(order.broker_order_id), request_id, "Revision"
    )
    if stale:
        return state, ledger, _decided(request, RequestOutcome.REFUSED, stale)
    if order.is_terminal:
        return (
            state,
            ledger,
            _decided(
                request,
                RequestOutcome.REFUSED,
                f"Order {order.broker_order_id} is {order.status}; a finished order is not "
                "amended.",
            ),
        )
    if canonical_status(order.status) is OrderStatus.CANCEL_PENDING:
        return (
            state,
            ledger,
            _decided(
                request,
                RequestOutcome.REFUSED,
                f"Order {order.broker_order_id} has a cancel in flight; amending it contradicts "
                "the cancel.",
            ),
        )
    if request.quantity <= order.filled_quantity:
        return (
            state,
            ledger,
            _decided(
                request,
                RequestOutcome.REFUSED,
                f"Amending order {order.broker_order_id} to {request.quantity} leaves nothing "
                f"working after the {order.filled_quantity} already filled; cancel it instead.",
            ),
        )
    if request.quantity == order.quantity and request.price == order.price:
        return (
            state,
            ledger,
            _decided(
                request,
                RequestOutcome.REFUSED,
                f"Order {order.broker_order_id} already stands at {order.quantity} @ "
                f"{order.price}; an amendment that changes nothing is not sent.",
            ),
        )

    return (
        state,
        replace(
            ledger,
            issued=ledger.issued.set(request_id, request),
            latest_revision=ledger.latest_revision.set(
                order.broker_order_id, (request.revision, request_id)
            ),
        ),
        _decided(request, RequestOutcome.NEW, ""),
    )
