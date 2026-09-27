"""The canonical order lifecycle: which events exist, and which transitions they may make.

AlphaLab has had one lifecycle *status* vocabulary since v2.0 --
:class:`~alphalab.core.enums.OrderStatus` -- and no statement of how an order
moves through it. The rules lived in the guards of
:class:`~alphalab.oms.order.Order`'s methods, and a second, slightly different
set lived in :mod:`alphalab.broker.reconciliation`, which applies a venue's fill
to any order that is not terminal. Nothing said which of the two was right, and
the one place they disagreed -- a fill arriving while a cancel is pending -- was
unreachable in the OMS and routine at a venue.

This module is the statement. :data:`ORDER_TRANSITIONS` is the whole of it: for
every status, the events that may move an order out of it and where each one
lands. The OMS reads it to decide what it permits, and the venue boundary
(:mod:`alphalab.broker.lifecycle`) reads it to decide what a reported event
means. One table, stated once, read by both the refusal and the act -- the shape
:data:`~alphalab.lifecycle.progression.LEGAL_PROGRESSION_TRANSITIONS` gave the
strategy progression in v3.5.

Intent, request, state, event
-----------------------------

Four things that are easy to collapse and must not be:

=============== =================================================================
**Intent**      What a strategy wants: :class:`~alphalab.strategy.events.Intent`.
**Request**     What AlphaLab proposes to send:
                :class:`~alphalab.core.order_request.OrderRequest`, and at the
                venue boundary a :class:`~alphalab.broker.order.BrokerOrder`
                staged for submission, a cancel or a modification.
**State**       Where an order *is*: :class:`~alphalab.core.enums.OrderStatus`.
**Event**       What a venue *reported*: an :class:`ExecutionEventKind`. An event
                is evidence about state; it is not state, and applying one is a
                decision this table makes, not an assignment.
=============== =================================================================

The events
----------

:class:`ExecutionEventKind` is the normalized vocabulary an adapter translates a
venue's messages into. Eight concern an order, two an account and two the
connection. Only the order events move an order, and only they appear in the
table; the other four are part of the vocabulary because an adapter reports them
through the same channel and a consumer must be able to tell them apart.

The two fill events are one fact split by arithmetic: ``ORDER_FILLED`` is a fill
that left nothing working and ``ORDER_PARTIALLY_FILLED`` is one that did, **as
the venue saw it when it happened**. Fills can arrive in a different order than
they occurred, and a venue's "this completed the order" describes its own
cumulative state at that instant, not the state of whoever receives the report.
So the name never decides anything: the quantities do, additively, and a fill
applied out of order lands exactly where it would have in order.
:mod:`alphalab.broker.lifecycle` records where a name and the arithmetic
disagreed, and a snapshot reconciliation settles whether a fill is missing.

What the table does not contain
-------------------------------

* **Idempotency.** An event that restates the current status is not a
  transition, so it has no entry; whether it is a harmless duplicate, a stale
  report the order has moved past, or a contradiction is decided by
  :func:`classify_order_event` from the table's shape, not by extra rows.
* **Requests.** ``CANCEL_PENDING`` is entered because *AlphaLab asked* for a
  cancel, not because a venue reported anything, so that edge is
  :data:`CANCEL_REQUESTABLE_STATUSES` rather than a row keyed by an event.
* **Broker-local statuses.** ``PENDING_SUBMIT``, ``SUBMITTED`` and
  ``PENDING_CANCEL`` exist only between AlphaLab and a venue (ADR-0012) and stay
  in :mod:`alphalab.broker.order`; the venue boundary maps each onto the status
  here that it behaves as.

Two decisions worth recording
-----------------------------

**A fill while a cancel is pending lands, and the cancel stays pending.** The
cancel and the fill crossed; the venue traded first, so the fill is real and is
applied. A fill that completes the order makes it ``FILLED``, and the cancel then
has nothing left to cancel. A fill that leaves quantity working leaves the order
``CANCEL_PENDING``: the cancel is still outstanding and may yet succeed
(``ORDER_CANCELLED``) or be refused (``ORDER_CANCEL_REJECTED``), and a status
that forgot it would invite a second cancel for an order that already has one
in flight. That is the precedence the institutional order-status convention
gives a pending cancel over a partial fill. Until v3.9 the OMS refused both
fills -- unreachably, because nothing in the OMS entered ``CANCEL_PENDING`` --
and :func:`~alphalab.broker.reconciliation.apply_execution` applied a partial one
by dropping the pending marker; both now read this row.

**A refused cancel returns the order to the working state its fills imply.**
``ACCEPTED`` and ``PARTIALLY_FILLED`` are one working state split by whether
anything has traded, so ``ORDER_CANCEL_REJECTED`` lands on whichever of the two
describes the order's filled quantity. ``CANCEL_PENDING`` is the one status that
does not say whether the order has traded, so it is also where "an order that
has traded cannot be rejected" has to read a quantity. Those two rules are why
:func:`next_order_status` takes one, and they are the only use it makes of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum, unique
from types import MappingProxyType
from typing import Final

from alphalab.core.enums import OrderStatus

__all__ = [
    "ACCOUNT_EVENT_KINDS",
    "CANCEL_REQUESTABLE_STATUSES",
    "CONNECTIVITY_EVENT_KINDS",
    "FILL_EVENT_KINDS",
    "ORDER_EVENT_KINDS",
    "ORDER_TRANSITIONS",
    "STATUS_EVENT_KINDS",
    "TERMINAL_ORDER_STATUSES",
    "WORKING_ORDER_STATUSES",
    "EventClassification",
    "ExecutionEventKind",
    "classify_order_event",
    "is_terminal_status",
    "next_order_status",
    "reachable_statuses",
    "working_status_for",
]


@unique
class ExecutionEventKind(StrEnum):
    """Everything a venue can report, normalized. The adapter's vocabulary."""

    #: The venue acknowledged the order and it is working.
    ORDER_ACCEPTED = "order_accepted"

    #: The venue refused the order. It never traded and never will.
    ORDER_REJECTED = "order_rejected"

    #: A fill that leaves quantity working.
    ORDER_PARTIALLY_FILLED = "order_partially_filled"

    #: A fill that leaves nothing working.
    ORDER_FILLED = "order_filled"

    #: The venue confirmed a cancel. Quantity already filled stays filled.
    ORDER_CANCELLED = "order_cancelled"

    #: The order's time in force ran out. Quantity already filled stays filled.
    ORDER_EXPIRED = "order_expired"

    #: The venue confirmed an amendment of the order's quantity or price.
    ORDER_REPLACED = "order_replaced"

    #: The venue refused a cancel request. The order is still working.
    ORDER_CANCEL_REJECTED = "order_cancel_rejected"

    #: The venue reports a position, absolutely -- the whole holding, not a delta.
    POSITION_CHANGED = "position_changed"

    #: The venue reports the account's balances, absolutely.
    BALANCE_CHANGED = "balance_changed"

    #: The connection to the venue went down. Nothing may be sent until it is up.
    BROKER_DISCONNECTED = "broker_disconnected"

    #: The connection came up. After a disconnect, what the venue holds is
    #: unconfirmed until a reconciliation says otherwise.
    BROKER_CONNECTED = "broker_connected"


#: The two events that carry a fill.
FILL_EVENT_KINDS: Final = frozenset(
    {ExecutionEventKind.ORDER_PARTIALLY_FILLED, ExecutionEventKind.ORDER_FILLED}
)

#: The order events that change status without carrying a fill or an amendment.
STATUS_EVENT_KINDS: Final = frozenset(
    {
        ExecutionEventKind.ORDER_ACCEPTED,
        ExecutionEventKind.ORDER_REJECTED,
        ExecutionEventKind.ORDER_CANCELLED,
        ExecutionEventKind.ORDER_EXPIRED,
        ExecutionEventKind.ORDER_CANCEL_REJECTED,
    }
)

#: Every event about one order. The only events :data:`ORDER_TRANSITIONS` is keyed by.
ORDER_EVENT_KINDS: Final = frozenset(
    {*FILL_EVENT_KINDS, *STATUS_EVENT_KINDS, ExecutionEventKind.ORDER_REPLACED}
)

#: Events about the account rather than an order.
ACCOUNT_EVENT_KINDS: Final = frozenset(
    {ExecutionEventKind.POSITION_CHANGED, ExecutionEventKind.BALANCE_CHANGED}
)

#: Events about the connection rather than an order or an account.
CONNECTIVITY_EVENT_KINDS: Final = frozenset(
    {ExecutionEventKind.BROKER_DISCONNECTED, ExecutionEventKind.BROKER_CONNECTED}
)

#: Statuses no event can move an order out of. The venue can never report another
#: fill for an order in one of these, and an event claiming otherwise is a
#: disagreement to surface, never a transition to make.
TERMINAL_ORDER_STATUSES: Final = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)

#: Statuses an order is still live in. The complement of the terminal set.
WORKING_ORDER_STATUSES: Final = frozenset(OrderStatus) - TERMINAL_ORDER_STATUSES

#: Statuses from which AlphaLab may *request* a cancel, entering ``CANCEL_PENDING``.
#:
#: Not an event: nothing was reported. It is listed so that
#: :func:`reachable_statuses` can see the edge -- an acknowledgement that arrives
#: after a cancel was requested is a report the order has moved past, not a
#: contradiction.
CANCEL_REQUESTABLE_STATUSES: Final = frozenset(
    {OrderStatus.NEW, OrderStatus.PENDING, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}
)

_E = ExecutionEventKind
_S = OrderStatus


def _row(
    entries: Mapping[ExecutionEventKind, OrderStatus],
) -> Mapping[ExecutionEventKind, OrderStatus]:
    return MappingProxyType(dict(entries))


#: Every legal transition: ``status -> {event: next status}``.
#:
#: Read by :meth:`alphalab.oms.order.Order`'s transitions and by
#: :mod:`alphalab.broker.lifecycle`. A pair absent from it is not a transition.
#: ``ORDER_REPLACED`` keeps the status it finds, which is why it maps each working
#: status to itself -- including ``CANCEL_PENDING``, because an amendment the
#: venue confirms is a fact about the order even while a cancel is in flight.
#: Whether AlphaLab may *ask* for one then is a different question, answered by
#: :func:`alphalab.broker.requests.issue_modify`, which refuses.
#:
#: Two rows read a quantity as well as a status, both through
#: :func:`next_order_status`: ``ORDER_REJECTED`` from ``CANCEL_PENDING`` only
#: while nothing has traded -- an order that has traded cannot be rejected, the
#: rule ``ACCEPTED`` and ``PARTIALLY_FILLED`` already encode by having and lacking
#: the row -- and ``ORDER_CANCEL_REJECTED``, which returns to the working status
#: the fills imply.
ORDER_TRANSITIONS: Final[Mapping[OrderStatus, Mapping[ExecutionEventKind, OrderStatus]]] = (
    MappingProxyType(
        {
            _S.NEW: _row(
                {
                    _E.ORDER_ACCEPTED: _S.ACCEPTED,
                    _E.ORDER_REJECTED: _S.REJECTED,
                    _E.ORDER_CANCELLED: _S.CANCELLED,
                    _E.ORDER_EXPIRED: _S.EXPIRED,
                    _E.ORDER_REPLACED: _S.NEW,
                }
            ),
            _S.PENDING: _row(
                {
                    _E.ORDER_ACCEPTED: _S.ACCEPTED,
                    _E.ORDER_REJECTED: _S.REJECTED,
                    _E.ORDER_CANCELLED: _S.CANCELLED,
                    _E.ORDER_EXPIRED: _S.EXPIRED,
                    _E.ORDER_REPLACED: _S.PENDING,
                }
            ),
            _S.ACCEPTED: _row(
                {
                    _E.ORDER_REJECTED: _S.REJECTED,
                    _E.ORDER_PARTIALLY_FILLED: _S.PARTIALLY_FILLED,
                    _E.ORDER_FILLED: _S.FILLED,
                    _E.ORDER_CANCELLED: _S.CANCELLED,
                    _E.ORDER_EXPIRED: _S.EXPIRED,
                    _E.ORDER_REPLACED: _S.ACCEPTED,
                }
            ),
            _S.PARTIALLY_FILLED: _row(
                {
                    _E.ORDER_PARTIALLY_FILLED: _S.PARTIALLY_FILLED,
                    _E.ORDER_FILLED: _S.FILLED,
                    _E.ORDER_CANCELLED: _S.CANCELLED,
                    _E.ORDER_EXPIRED: _S.EXPIRED,
                    _E.ORDER_REPLACED: _S.PARTIALLY_FILLED,
                }
            ),
            _S.CANCEL_PENDING: _row(
                {
                    _E.ORDER_REJECTED: _S.REJECTED,
                    _E.ORDER_PARTIALLY_FILLED: _S.CANCEL_PENDING,
                    _E.ORDER_FILLED: _S.FILLED,
                    _E.ORDER_CANCELLED: _S.CANCELLED,
                    _E.ORDER_EXPIRED: _S.EXPIRED,
                    _E.ORDER_REPLACED: _S.CANCEL_PENDING,
                    _E.ORDER_CANCEL_REJECTED: _S.ACCEPTED,
                }
            ),
            _S.FILLED: _row({}),
            _S.CANCELLED: _row({}),
            _S.REJECTED: _row({}),
            _S.EXPIRED: _row({}),
        }
    )
)


def is_terminal_status(status: OrderStatus) -> bool:
    """Whether no event can move an order out of ``status``."""

    return status in TERMINAL_ORDER_STATUSES


def working_status_for(filled_quantity: Decimal) -> OrderStatus:
    """The working status an order with this filled quantity is in.

    ``ACCEPTED`` and ``PARTIALLY_FILLED`` are one working state split by whether
    anything has traded. Read wherever an order returns to working without a
    fill saying which -- a refused cancel.
    """

    return OrderStatus.PARTIALLY_FILLED if filled_quantity > 0 else OrderStatus.ACCEPTED


def next_order_status(
    status: OrderStatus, event: ExecutionEventKind, filled_quantity: Decimal
) -> OrderStatus | None:
    """Where ``event`` moves an order in ``status``, or ``None`` if it may not.

    ``filled_quantity`` is the order's filled quantity *before* the event. It
    decides two transitions and no others: ``ORDER_CANCEL_REJECTED`` returns an
    order to :func:`working_status_for` its fills, and ``ORDER_REJECTED`` is
    refused for an order that has traded. Every other target is the table's.
    """

    target = ORDER_TRANSITIONS[status].get(event)
    if target is None:
        return None
    if event is ExecutionEventKind.ORDER_CANCEL_REJECTED:
        return working_status_for(filled_quantity)
    if event is ExecutionEventKind.ORDER_REJECTED and filled_quantity > 0:
        return None
    return target


def _successors(status: OrderStatus) -> frozenset[OrderStatus]:
    successors = set(ORDER_TRANSITIONS[status].values())
    if status in CANCEL_REQUESTABLE_STATUSES:
        successors.add(OrderStatus.CANCEL_PENDING)
    if status is OrderStatus.CANCEL_PENDING:
        # A refused cancel lands on either working status; the table spells the
        # pair once, as ACCEPTED, and next_order_status picks by quantity.
        successors.add(OrderStatus.PARTIALLY_FILLED)
    return frozenset(successors)


def _closure() -> Mapping[OrderStatus, frozenset[OrderStatus]]:
    reached: dict[OrderStatus, frozenset[OrderStatus]] = {}
    for start in OrderStatus:
        seen: set[OrderStatus] = set()
        frontier = list(_successors(start))
        while frontier:
            status = frontier.pop()
            if status in seen:
                continue
            seen.add(status)
            frontier.extend(_successors(status))
        reached[start] = frozenset(seen)
    return MappingProxyType(reached)


#: Computed once at import: the table is fixed, so its closure is too.
_REACHABLE: Final = _closure()


def reachable_statuses(status: OrderStatus) -> frozenset[OrderStatus]:
    """Every status an order in ``status`` can still reach, by events or a cancel request.

    ``status`` itself is included only when a cycle returns to it -- a working
    order can reach itself through an amendment. Used to tell a stale report,
    whose status the order has already moved past, from a contradictory one.
    """

    return _REACHABLE[status]


@unique
class EventClassification(StrEnum):
    """What a status-changing event is, relative to the status an order holds."""

    #: A legal transition.
    TRANSITION = "transition"

    #: The order already holds the status the event reports. Nothing to do.
    DUPLICATE = "duplicate"

    #: The order has already moved past the status the event reports: a report
    #: delivered late. Nothing to do, and nothing wrong.
    STALE = "stale"

    #: The event reports a status the order cannot reach and has not passed
    #: through. The two sides disagree; this is a break.
    CONFLICT = "conflict"


def classify_order_event(
    status: OrderStatus, event: ExecutionEventKind, filled_quantity: Decimal
) -> tuple[EventClassification, OrderStatus]:
    """Classify a status-changing event against the status an order holds.

    Total over :data:`STATUS_EVENT_KINDS` and every status: each pair has
    exactly one answer, decided in this order --

    1. the table permits it: a ``TRANSITION`` to the table's target;
    2. the order already holds the reported status: ``DUPLICATE``;
    3. the order has moved past it -- the current status is reachable from the
       reported one: ``STALE``;
    4. otherwise ``CONFLICT``.

    Returns the classification and the status the event *reports* (for a
    transition, the one the order moves to).

    Raises:
        ValueError: If ``event`` is not a status-changing event. A fill is
            classified by quantity and an amendment by its values, and neither
            has a status of its own to compare.
    """

    if event not in STATUS_EVENT_KINDS:
        raise ValueError(
            f"{event} is not a status-changing event; fills and amendments are "
            "classified by their quantities and values, not by the status they report."
        )

    target = next_order_status(status, event, filled_quantity)
    if target is not None:
        return EventClassification.TRANSITION, target

    reported = (
        working_status_for(filled_quantity)
        if event is ExecutionEventKind.ORDER_CANCEL_REJECTED
        else _REPORTED[event]
    )
    if reported is status:
        return EventClassification.DUPLICATE, reported
    if status in reachable_statuses(reported):
        return EventClassification.STALE, reported
    return EventClassification.CONFLICT, reported


#: The status each non-fill status event reports, independent of where it is applied.
_REPORTED: Final[Mapping[ExecutionEventKind, OrderStatus]] = MappingProxyType(
    {
        ExecutionEventKind.ORDER_ACCEPTED: OrderStatus.ACCEPTED,
        ExecutionEventKind.ORDER_REJECTED: OrderStatus.REJECTED,
        ExecutionEventKind.ORDER_CANCELLED: OrderStatus.CANCELLED,
        ExecutionEventKind.ORDER_EXPIRED: OrderStatus.EXPIRED,
    }
)
