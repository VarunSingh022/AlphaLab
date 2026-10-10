"""Applying what a venue reported to AlphaLab's mirror of it, one normalized event at a time.

An adapter translates whatever its venue sends -- a FIX execution report, a JSON
message, a socket frame -- into a :class:`VenueEvent`, whose ``kind`` is one of
the :class:`~alphalab.core.lifecycle.ExecutionEventKind` values every adapter
shares. This module decides what that event *means* for
:class:`~alphalab.broker.state.BrokerState` and applies it. Nothing here knows
which venue sent the event, and nothing needs to: the vendor's protocol ends at
the adapter, and the contract starts here.

Until v3.9 only one kind of report had a defined application. A fill went
through :func:`~alphalab.broker.reconciliation.apply_execution`, which answers
redelivery, unknown orders, terminal orders and overfills. An acknowledgement, a
rejection, a cancel, an expiry, an amendment, a position, a balance and a
disconnect had none: each adapter did what it chose inside its own
:class:`~alphalab.broker.protocol.BrokerProtocol` methods, and two adapters
could leave the mirror in two different states from the same messages.

Every event gets exactly one outcome
------------------------------------

:func:`classify_venue_event` is total and pure:

================== ==============================================================
``APPLIED``        The event moved the mirror.
``DUPLICATE``      The mirror already reflects it -- the same fill by
                   ``execution_id``, the status the order already holds, the
                   amendment already made. Returned unchanged; a redelivery is
                   routine, not a fault.
``STALE``          The order has already moved past what the event reports: a
                   late acknowledgement after a fill, a refused cancel after the
                   order filled. Returned unchanged, and not a break.
``CONFLICT``       The event contradicts what the mirror holds -- a cancel for a
                   filled order, a rejection for one that traded, a fill against
                   a finished order or beyond what was ordered. Not applied, and
                   a break:
                   the caller resolves it with a reconciliation, as
                   :mod:`alphalab.broker.reconciliation` already requires of a
                   fill against a terminal order.
``UNKNOWN_ORDER``  The event names an order the mirror has never seen. A break.
``INVALID``        The event is malformed for what it claims: a non-positive
                   fill, a negative price, a balance in another account.
================== ==============================================================

Order events are classified by the table in :mod:`alphalab.core.lifecycle`, the
one the OMS reads, through :func:`~alphalab.core.lifecycle.classify_order_event`.
A broker-local status is read as the canonical status it behaves as
(:data:`~alphalab.broker.order.BROKER_LOCAL_EQUIVALENTS`), each of which is one
of the statuses :data:`~alphalab.broker.order.BROKER_STATUS_EQUIVALENTS` already
calls consistent with it.

Ordering, and what cannot be ordered
------------------------------------

**Status events are ordered by the lifecycle itself.** A report whose status the
order has already passed is ``STALE``, whichever order the network delivered it
in, so reordering them does not change where the order ends up.

**Fills are additive**, which ``broker.reconciliation`` has held since v2.3:
two fills applied in either order produce the same order, and out-of-order
delivery needs no handling.

**A fill proves acceptance.** A venue cannot fill an order it did not accept, so
a fill for an order still awaiting its acknowledgement is applied, and the
decision records the acknowledgement it implies. The late acknowledgement, when
it arrives, is ``STALE``.

**A fill's name does not outrank its quantity.** ``ORDER_FILLED`` says the fill
completed the order *as the venue saw it*; delivered ahead of an earlier partial
fill it leaves quantity working in the mirror, and refusing it would break the
additivity every out-of-order case above relies on. The quantities decide, the
decision's ``reason`` records the disagreement, and a missing fill shows up
where it belongs -- as a filled-quantity difference in a snapshot reconciliation.

**Amendments, positions and balances are absolute, and a venue sequence orders
them.** Each replaces a value rather than adding to one, so two delivered out of
order would leave the earlier value in place. Since v3.11 an event may carry the
venue's own ``sequence`` number, and the mirror records, per amended order, per
position and for the balances, the number of the last report it applied
(:attr:`~alphalab.broker.state.BrokerState.venue_sequences`):

* a report numbered **below** the recorded one is ``STALE`` -- the newer value is
  already in place;
* a report numbered **the same** is a ``DUPLICATE`` when the mirror holds what it
  says, and a ``CONFLICT`` when it does not -- a venue does not give two
  different values one number;
* a report numbered **above** is applied and recorded, even when its value is
  the one the mirror already holds: a later report restating a value is still
  the latest word, and an older one delivered after it must read as stale.

A sequence compares only within one connection session -- a venue may number a
new session afresh -- so an applied ``BROKER_CONNECTED`` clears every recorded
number, and the snapshot reconciliation a connection requires anyway
(:attr:`LifecycleDecision.resync_required`) covers the gap. Once a venue numbers
an entity's reports, an unnumbered report about it cannot be ordered against
them and is ``INVALID``. A venue that numbers nothing leaves these reports in
delivery order: delivering them in venue order is then the adapter's
obligation, stated rather than assumed, and
:func:`~alphalab.broker.reconciliation.reconcile_snapshot` is the check. Status
events and fills may carry the venue's number as well; it is not compared,
because the lifecycle and additivity already order them.

What this module does not do
----------------------------

It appends nothing to :attr:`~alphalab.broker.state.BrokerState.events`. That
log is the adapter's record of what it emitted and its snapshot reads a closed
set of event types, so a new type in it would be a schema change; the normalized
events, and the decisions made about them, are the caller's to keep -- exactly
as :func:`~alphalab.broker.reconciliation.apply_execution` leaves them.

It does not touch AlphaLab's own book. The mirror and the book are two things,
reconciled against each other by
:func:`~alphalab.lifecycle.reconciliation.reconcile_execution_state`; a fill
reaches the portfolio through
:func:`~alphalab.runtime.broker_routing.apply_broker_execution` and a terminal
outcome through
:meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.apply_terminal_outcome`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum, unique
from types import MappingProxyType
from typing import Final

from alphalab.broker.account import BrokerAccount
from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus, canonical_status
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import (
    ExecutionDecision,
    ExecutionOutcome,
    apply_execution,
    classify_execution,
)
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import OrderStatus
from alphalab.core.lifecycle import (
    ACCOUNT_EVENT_KINDS,
    CONNECTIVITY_EVENT_KINDS,
    FILL_EVENT_KINDS,
    ORDER_EVENT_KINDS,
    ORDER_TRANSITIONS,
    STATUS_EVENT_KINDS,
    EventClassification,
    ExecutionEventKind,
    classify_order_event,
)

__all__ = [
    "LifecycleDecision",
    "LifecycleOutcome",
    "VenueEvent",
    "apply_venue_event",
    "apply_venue_events",
    "classify_venue_event",
]

#: Statuses an order is in before the venue has acknowledged it.
_UNACKNOWLEDGED: Final = frozenset({OrderStatus.NEW, OrderStatus.PENDING})

#: The key the balances' venue sequence is recorded under. An amendment's is
#: ``order:<broker_order_id>`` and a position's ``position:<symbol>``.
_BALANCES: Final = "account"


@dataclass(frozen=True, slots=True)
class VenueEvent:
    """One normalized report from a venue, as an adapter translated it.

    One type for every kind, carrying exactly the payload its kind needs, which
    construction checks. That is the shape the institutional execution report
    has always had -- one message, an execution type saying what happened --
    and it is what lets a consumer handle a stream of them without knowing which
    adapter produced it.

    Attributes:
        kind: What happened.
        timestamp: When the *venue* says it happened, in Unix seconds. Never a
            local clock reading: it is the time that reaches the mirror order.
        broker_order_id: The venue handle the event is about. Required for every
            order event and empty for every other.
        execution: The fill, for the two fill kinds. Its ``execution_id`` is the
            identity redelivery is recognised by.
        quantity: The order's new total quantity, for ``ORDER_REPLACED``.
        price: The order's new price, for ``ORDER_REPLACED``.
        position: The venue's whole position in one instrument, for
            ``POSITION_CHANGED``.
        account: The venue's account balances, for ``BALANCE_CHANGED``.
        reason: What the venue said, verbatim, for a rejection, a refused cancel
            or a disconnect. Carried, never interpreted.
        sequence: The venue's own number for the report, when it numbers them;
            ``None`` when it does not. Orders the absolute reports -- an
            amendment, a position, the balances -- against each other; see the
            module docstring. A connectivity event carries none: it opens or
            closes the session the numbers are counted in.

    Raises:
        BrokerValidationError: If the timestamp is not finite, the payload does
            not match the kind -- a fill with no execution, an amendment with no
            quantity, an order event naming no order -- or the sequence is not a
            non-negative integer, or is given for a connectivity event.
    """

    kind: ExecutionEventKind
    timestamp: float
    broker_order_id: str = ""
    execution: BrokerExecution | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    position: BrokerPosition | None = None
    account: BrokerAccount | None = None
    reason: str = ""
    sequence: int | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.timestamp):
            raise BrokerValidationError(
                f"A {self.kind} event stamped {self.timestamp!r} has no instant."
            )
        if self.sequence is not None:
            if (
                isinstance(self.sequence, bool)
                or not isinstance(self.sequence, int)
                or self.sequence < 0
            ):
                raise BrokerValidationError(
                    f"A venue sequence is a non-negative integer, got {self.sequence!r}."
                )
            if self.kind in CONNECTIVITY_EVENT_KINDS:
                raise BrokerValidationError(
                    f"A {self.kind} event opens or closes the session a venue numbers its "
                    "reports in; it carries no sequence of its own."
                )
        carried = {
            "execution": self.execution is not None,
            "quantity": self.quantity is not None,
            "price": self.price is not None,
            "position": self.position is not None,
            "account": self.account is not None,
        }
        if self.kind in FILL_EVENT_KINDS:
            needed = {"execution"}
        elif self.kind is ExecutionEventKind.ORDER_REPLACED:
            needed = {"quantity", "price"}
        elif self.kind is ExecutionEventKind.POSITION_CHANGED:
            needed = {"position"}
        elif self.kind is ExecutionEventKind.BALANCE_CHANGED:
            needed = {"account"}
        else:
            needed = set()
        present = sorted(name for name, held in carried.items() if held)
        if set(present) != needed:
            raise BrokerValidationError(
                f"A {self.kind} event carries {present}; it needs exactly {sorted(needed)}."
            )
        if self.kind in ORDER_EVENT_KINDS:
            if not self.broker_order_id:
                raise BrokerValidationError(f"A {self.kind} event names no order.")
        elif self.broker_order_id:
            raise BrokerValidationError(
                f"A {self.kind} event is not about an order, and names {self.broker_order_id!r}."
            )
        if self.execution is not None and self.execution.broker_order_id != self.broker_order_id:
            raise BrokerValidationError(
                f"A {self.kind} event about {self.broker_order_id!r} carries a fill for "
                f"{self.execution.broker_order_id!r}."
            )


@unique
class LifecycleOutcome(StrEnum):
    """What a reported event turned out to be. See the module docstring."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    STALE = "stale"
    CONFLICT = "conflict"
    UNKNOWN_ORDER = "unknown_order"
    INVALID = "invalid"


#: The outcomes that mean AlphaLab and the venue disagree.
_BREAKS: Final = frozenset(
    {LifecycleOutcome.CONFLICT, LifecycleOutcome.UNKNOWN_ORDER, LifecycleOutcome.INVALID}
)


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    """The classification of one venue event, and why.

    Attributes:
        event: The event classified.
        outcome: What it turned out to be.
        reason: One sentence. Empty only for a plain ``APPLIED``.
        status_before: The mirror order's status before the event, for an order
            event on a known order.
        status_after: Its status after -- the same as before unless applied.
        implied: Events the applied one proves happened first. A fill for an
            unacknowledged order implies ``ORDER_ACCEPTED``.
        execution: For a fill, the classification
            :func:`~alphalab.broker.reconciliation.classify_execution` gave it.
    """

    event: VenueEvent
    outcome: LifecycleOutcome
    reason: str
    status_before: OrderStatus | BrokerOrderStatus | None = None
    status_after: OrderStatus | BrokerOrderStatus | None = None
    implied: tuple[ExecutionEventKind, ...] = ()
    execution: ExecutionDecision | None = None

    @property
    def applied(self) -> bool:
        """Whether the event changed the mirror."""

        return self.outcome is LifecycleOutcome.APPLIED

    @property
    def is_break(self) -> bool:
        """Whether AlphaLab and the venue disagree, and a reconciliation must resolve it.

        A duplicate or a stale report is not a break: redelivery and reordering
        are what a network does.
        """

        return self.outcome in _BREAKS

    @property
    def resync_required(self) -> bool:
        """Whether the mirror must be reconciled before another order is sent.

        True after a connection comes up. ADR-0012: an adapter that has just
        (re)connected "has not confirmed what the venue already holds", and a
        snapshot reconciliation is how it confirms it.
        """

        return self.applied and self.event.kind is ExecutionEventKind.BROKER_CONNECTED


def _decision(
    event: VenueEvent,
    outcome: LifecycleOutcome,
    reason: str,
    order: BrokerOrder | None = None,
    after: OrderStatus | BrokerOrderStatus | None = None,
    implied: tuple[ExecutionEventKind, ...] = (),
    execution: ExecutionDecision | None = None,
) -> LifecycleDecision:
    before = None if order is None else order.status
    return LifecycleDecision(
        event=event,
        outcome=outcome,
        reason=reason,
        status_before=before,
        status_after=before if after is None else after,
        implied=implied,
        execution=execution,
    )


def _status_event(state: BrokerState, event: VenueEvent) -> tuple[BrokerState, LifecycleDecision]:
    order = state.orders.get(event.broker_order_id)
    if order is None:
        return state, _decision(
            event,
            LifecycleOutcome.UNKNOWN_ORDER,
            f"{event.kind} names order {event.broker_order_id!r}, which the mirror has never held.",
        )

    current = canonical_status(order.status)
    classification, reported = classify_order_event(current, event.kind, order.filled_quantity)
    if classification is EventClassification.TRANSITION:
        moved = replace(order, status=reported, updated_at=max(order.updated_at, event.timestamp))
        return (
            replace(state, orders=state.orders.set(order.broker_order_id, moved)),
            _decision(event, LifecycleOutcome.APPLIED, "", order, reported),
        )
    if classification is EventClassification.DUPLICATE:
        return state, _decision(
            event,
            LifecycleOutcome.DUPLICATE,
            f"Order {order.broker_order_id} is already {reported}; the report restates it.",
            order,
        )
    if classification is EventClassification.STALE:
        return state, _decision(
            event,
            LifecycleOutcome.STALE,
            f"Order {order.broker_order_id} is {order.status}, past the {reported} this report "
            "describes; it arrived late and changes nothing.",
            order,
        )
    return state, _decision(
        event,
        LifecycleOutcome.CONFLICT,
        f"The venue reports order {order.broker_order_id} {reported} and the mirror holds it "
        f"{order.status} with {order.filled_quantity} filled; no transition joins the two, so "
        "they disagree.",
        order,
    )


_EXECUTION_OUTCOMES: Final[Mapping[ExecutionOutcome, LifecycleOutcome]] = MappingProxyType(
    {
        ExecutionOutcome.APPLIED: LifecycleOutcome.APPLIED,
        ExecutionOutcome.DUPLICATE: LifecycleOutcome.DUPLICATE,
        ExecutionOutcome.UNKNOWN_ORDER: LifecycleOutcome.UNKNOWN_ORDER,
        ExecutionOutcome.TERMINAL_ORDER: LifecycleOutcome.CONFLICT,
        ExecutionOutcome.OVERFILL: LifecycleOutcome.CONFLICT,
        ExecutionOutcome.INVALID: LifecycleOutcome.INVALID,
    }
)


def _fill_event(state: BrokerState, event: VenueEvent) -> tuple[BrokerState, LifecycleDecision]:
    execution = event.execution
    assert execution is not None  # construction guarantees it
    order = state.orders.get(execution.broker_order_id)
    verdict = classify_execution(state, execution)
    outcome = _EXECUTION_OUTCOMES[verdict.outcome]
    if outcome is not LifecycleOutcome.APPLIED:
        return state, _decision(event, outcome, verdict.reason, order, execution=verdict)

    assert order is not None  # an applicable fill has an order
    remaining = order.quantity - order.filled_quantity - execution.fill_quantity
    claims_complete = event.kind is ExecutionEventKind.ORDER_FILLED
    note = (
        ""
        if (remaining == 0) is claims_complete
        else f"The venue named {execution.execution_id} {event.kind}; with the fills delivered "
        f"so far it leaves {remaining} of order {order.broker_order_id} working. The "
        "quantities decide -- a venue names a fill by its own cumulative state, which "
        "out-of-order delivery can put ahead of the mirror's."
    )

    applied_state, _, _ = apply_execution(state, execution)
    after = applied_state.orders[order.broker_order_id].status
    implied = (
        (ExecutionEventKind.ORDER_ACCEPTED,)
        if canonical_status(order.status) in _UNACKNOWLEDGED
        else ()
    )
    return applied_state, _decision(
        event, LifecycleOutcome.APPLIED, note, order, after, implied, verdict
    )


def _sequence_gate(
    state: BrokerState,
    event: VenueEvent,
    key: str,
    subject: str,
    holds_it: bool,
    order: BrokerOrder | None = None,
) -> LifecycleDecision | None:
    """What the venue sequence alone decides about an absolute report, if anything.

    ``None`` means the sequence permits applying the report: it is unnumbered for
    an entity the venue has not numbered, the first numbered report about it, or
    newer than the last one applied. ``holds_it`` says whether the mirror already
    holds the value the report carries.
    """

    last = state.venue_sequences.get(key)
    if event.sequence is None:
        if last is None:
            return None
        return _decision(
            event,
            LifecycleOutcome.INVALID,
            f"The venue numbers its reports about {subject} and the mirror applied number "
            f"{last}; a report with no number cannot be ordered against it.",
            order,
        )
    if last is None or event.sequence > last:
        return None
    if event.sequence < last:
        return _decision(
            event,
            LifecycleOutcome.STALE,
            f"Report {event.sequence} about {subject} predates report {last}, which the "
            "mirror already applied; it arrived late and changes nothing.",
            order,
        )
    if holds_it:
        return _decision(
            event,
            LifecycleOutcome.DUPLICATE,
            f"The mirror applied report {last} about {subject} and holds what it says.",
            order,
        )
    return _decision(
        event,
        LifecycleOutcome.CONFLICT,
        f"The venue gave two different reports about {subject} the number {last}; the mirror "
        "holds the first, and a reconciliation must decide which is true.",
        order,
    )


def _numbered(state: BrokerState, event: VenueEvent, key: str) -> BrokerState:
    """``state`` with the event's sequence recorded as the last applied for ``key``."""

    if event.sequence is None:
        return state
    return replace(state, venue_sequences=state.venue_sequences.set(key, event.sequence))


def _restated(
    state: BrokerState,
    event: VenueEvent,
    key: str,
    subject: str,
    order: BrokerOrder | None = None,
) -> tuple[BrokerState, LifecycleDecision]:
    """A report restating the value the mirror holds.

    Unnumbered, it is a ``DUPLICATE`` and changes nothing. Numbered -- and the gate
    has already established the number is new -- it is the venue's latest word
    about the entity, and recording that is a change: an older report delivered
    after it must read as stale.
    """

    if event.sequence is None:
        return state, _decision(
            event, LifecycleOutcome.DUPLICATE, f"The mirror already holds {subject}.", order
        )
    return _numbered(state, event, key), _decision(
        event,
        LifecycleOutcome.APPLIED,
        f"The mirror already holds what report {event.sequence} says about {subject}; it is "
        "recorded as the latest one.",
        order,
    )


def _replaced(state: BrokerState, event: VenueEvent) -> tuple[BrokerState, LifecycleDecision]:
    quantity, price = event.quantity, event.price
    assert quantity is not None and price is not None  # construction guarantees them
    order = state.orders.get(event.broker_order_id)
    if order is None:
        return state, _decision(
            event,
            LifecycleOutcome.UNKNOWN_ORDER,
            f"An amendment names order {event.broker_order_id!r}, which the mirror has never held.",
        )
    if quantity <= 0 or price < 0:
        return state, _decision(
            event,
            LifecycleOutcome.INVALID,
            f"An amendment to quantity {quantity} at price {price} is not an order.",
            order,
        )
    key = f"order:{order.broker_order_id}"
    subject = f"order {order.broker_order_id}"
    stands = quantity == order.quantity and price == order.price
    gated = _sequence_gate(state, event, key, subject, stands, order)
    if gated is not None:
        return state, gated
    if stands:
        return _restated(
            state, event, key, f"order {order.broker_order_id} at {quantity} @ {price}", order
        )
    current = canonical_status(order.status)
    if ExecutionEventKind.ORDER_REPLACED not in ORDER_TRANSITIONS[current]:
        return state, _decision(
            event,
            LifecycleOutcome.CONFLICT,
            f"The venue reports an amendment to order {order.broker_order_id}, which the mirror "
            f"holds {order.status}; a finished order has nothing to amend.",
            order,
        )
    if quantity <= order.filled_quantity:
        return state, _decision(
            event,
            LifecycleOutcome.CONFLICT,
            f"The venue amends order {order.broker_order_id} to {quantity}, and the mirror has "
            f"{order.filled_quantity} filled; a working order with nothing left to work is a "
            "contradiction a reconciliation must resolve.",
            order,
        )
    moved = replace(
        order,
        quantity=quantity,
        price=price,
        updated_at=max(order.updated_at, event.timestamp),
    )
    amended = replace(state, orders=state.orders.set(order.broker_order_id, moved))
    return (
        _numbered(amended, event, key),
        _decision(event, LifecycleOutcome.APPLIED, "", order, order.status),
    )


def _account_event(state: BrokerState, event: VenueEvent) -> tuple[BrokerState, LifecycleDecision]:
    if event.kind is ExecutionEventKind.POSITION_CHANGED:
        position = event.position
        assert position is not None  # construction guarantees it
        if position.account_id not in ("", state.account.account_id):
            return state, _decision(
                event,
                LifecycleOutcome.INVALID,
                f"A position for account {position.account_id!r} is not this mirror's; it holds "
                f"{state.account.account_id!r}.",
            )
        key = f"position:{position.symbol}"
        subject = f"the position in {position.symbol}"
        held = state.positions.get(position.symbol) == position
        gated = _sequence_gate(state, event, key, subject, held)
        if gated is not None:
            return state, gated
        if held:
            return _restated(state, event, key, f"this position in {position.symbol}")
        return (
            _numbered(
                replace(state, positions=state.positions.set(position.symbol, position)),
                event,
                key,
            ),
            _decision(event, LifecycleOutcome.APPLIED, ""),
        )

    account = event.account
    assert account is not None  # construction guarantees it
    if account.account_id != state.account.account_id:
        return state, _decision(
            event,
            LifecycleOutcome.INVALID,
            f"Balances for account {account.account_id!r} are not this mirror's; it holds "
            f"{state.account.account_id!r}.",
        )
    if account.currency != state.account.currency:
        return state, _decision(
            event,
            LifecycleOutcome.INVALID,
            f"Balances in {account.currency} are not a change to an account denominated in "
            f"{state.account.currency}; a currency is named, never converted by assignment.",
        )
    held = account == state.account
    gated = _sequence_gate(state, event, _BALANCES, "the balances", held)
    if gated is not None:
        return state, gated
    if held:
        return _restated(state, event, _BALANCES, "these balances")
    return _numbered(replace(state, account=account), event, _BALANCES), _decision(
        event, LifecycleOutcome.APPLIED, ""
    )


def _connectivity_event(
    state: BrokerState, event: VenueEvent
) -> tuple[BrokerState, LifecycleDecision]:
    target = (
        ConnectionStatus.CONNECTED
        if event.kind is ExecutionEventKind.BROKER_CONNECTED
        else ConnectionStatus.DISCONNECTED
    )
    if state.connection_status is target:
        return state, _decision(
            event, LifecycleOutcome.DUPLICATE, f"The connection is already {target.name}."
        )
    if target is ConnectionStatus.CONNECTED:
        # A new session: the venue may number its reports afresh.
        return replace(state, connection_status=target, venue_sequences=PersistentMap()), _decision(
            event,
            LifecycleOutcome.APPLIED,
            "Connected. What the venue holds is unconfirmed until a snapshot is reconciled, "
            "and no report numbered in an earlier session is compared with this one's.",
        )
    return replace(state, connection_status=target), _decision(
        event,
        LifecycleOutcome.APPLIED,
        f"Disconnected: {event.reason or 'no reason given'}. No order may be sent.",
    )


def _evaluate(state: BrokerState, event: VenueEvent) -> tuple[BrokerState, LifecycleDecision]:
    if event.kind in STATUS_EVENT_KINDS:
        return _status_event(state, event)
    if event.kind in FILL_EVENT_KINDS:
        return _fill_event(state, event)
    if event.kind is ExecutionEventKind.ORDER_REPLACED:
        return _replaced(state, event)
    if event.kind in ACCOUNT_EVENT_KINDS:
        return _account_event(state, event)
    assert event.kind in CONNECTIVITY_EVENT_KINDS
    return _connectivity_event(state, event)


def classify_venue_event(state: BrokerState, event: VenueEvent) -> LifecycleDecision:
    """Decide what a venue event is, without changing anything.

    Pure and total: every event gets exactly one :class:`LifecycleOutcome`, and
    the same event against the same state always gets the same one.
    """

    return _evaluate(state, event)[1]


def apply_venue_event(
    state: BrokerState, event: VenueEvent
) -> tuple[BrokerState, LifecycleDecision]:
    """Apply a venue event to the mirror, if it may be applied.

    Anything but ``APPLIED`` returns ``state`` itself, so applying the same event
    twice is safe -- the second application is a ``DUPLICATE`` -- and a refused
    event can be retried after a reconciliation without having half-applied.
    """

    return _evaluate(state, event)


def apply_venue_events(
    state: BrokerState, events: Iterable[VenueEvent]
) -> tuple[BrokerState, tuple[LifecycleDecision, ...]]:
    """Apply events in the order given, returning the final mirror and every decision."""

    decisions: list[LifecycleDecision] = []
    current = state
    for event in events:
        current, decision = _evaluate(current, event)
        decisions.append(decision)
    return current, tuple(decisions)
