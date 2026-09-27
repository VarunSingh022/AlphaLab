"""The canonical order lifecycle: the table, its classification, and the OMS reading it."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.core.lifecycle import (
    ACCOUNT_EVENT_KINDS,
    CANCEL_REQUESTABLE_STATUSES,
    CONNECTIVITY_EVENT_KINDS,
    FILL_EVENT_KINDS,
    ORDER_EVENT_KINDS,
    ORDER_TRANSITIONS,
    STATUS_EVENT_KINDS,
    TERMINAL_ORDER_STATUSES,
    WORKING_ORDER_STATUSES,
    EventClassification,
    ExecutionEventKind,
    classify_order_event,
    is_terminal_status,
    next_order_status,
    reachable_statuses,
    working_status_for,
)
from alphalab.oms.exceptions import InvalidTransitionError
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order

E = ExecutionEventKind
S = OrderStatus
ZERO = Decimal("0")
SOME = Decimal("3")


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #


def test_the_event_families_partition_the_vocabulary() -> None:
    families = [ORDER_EVENT_KINDS, ACCOUNT_EVENT_KINDS, CONNECTIVITY_EVENT_KINDS]
    assert frozenset().union(*families) == frozenset(ExecutionEventKind)
    for left, right in itertools.combinations(families, 2):
        assert not left & right
    assert FILL_EVENT_KINDS | STATUS_EVENT_KINDS | {E.ORDER_REPLACED} == ORDER_EVENT_KINDS


def test_the_named_events_the_contract_requires_exist() -> None:
    names = {kind.name for kind in ExecutionEventKind}
    assert {
        "ORDER_ACCEPTED",
        "ORDER_REJECTED",
        "ORDER_PARTIALLY_FILLED",
        "ORDER_FILLED",
        "ORDER_CANCELLED",
        "ORDER_EXPIRED",
        "POSITION_CHANGED",
        "BALANCE_CHANGED",
        "BROKER_DISCONNECTED",
    } <= names


def test_terminal_and_working_statuses_partition_every_status() -> None:
    assert frozenset(OrderStatus) == TERMINAL_ORDER_STATUSES | WORKING_ORDER_STATUSES
    assert not TERMINAL_ORDER_STATUSES & WORKING_ORDER_STATUSES
    assert {S.FILLED, S.CANCELLED, S.REJECTED, S.EXPIRED} == TERMINAL_ORDER_STATUSES
    for status in OrderStatus:
        assert is_terminal_status(status) is (status in TERMINAL_ORDER_STATUSES)


# --------------------------------------------------------------------------- #
# The table
# --------------------------------------------------------------------------- #


def test_every_status_has_a_row_and_only_order_events_key_it() -> None:
    assert set(ORDER_TRANSITIONS) == set(OrderStatus)
    for row in ORDER_TRANSITIONS.values():
        assert set(row) <= ORDER_EVENT_KINDS


def test_terminal_statuses_have_no_way_out() -> None:
    for status in TERMINAL_ORDER_STATUSES:
        assert ORDER_TRANSITIONS[status] == {}
        for event in ORDER_EVENT_KINDS:
            assert next_order_status(status, event, ZERO) is None
            assert next_order_status(status, event, SOME) is None


def test_every_working_status_can_be_cancelled_and_expired() -> None:
    for status in WORKING_ORDER_STATUSES:
        assert next_order_status(status, E.ORDER_CANCELLED, ZERO) is S.CANCELLED
        assert next_order_status(status, E.ORDER_EXPIRED, ZERO) is S.EXPIRED


@pytest.mark.parametrize(
    ("status", "event", "target"),
    [
        (status, event, target)
        for status, row in ORDER_TRANSITIONS.items()
        for event, target in row.items()
        if event not in {E.ORDER_REJECTED, E.ORDER_CANCEL_REJECTED}
    ],
)
def test_every_legal_transition_lands_where_the_table_says(
    status: OrderStatus, event: ExecutionEventKind, target: OrderStatus
) -> None:
    assert next_order_status(status, event, ZERO) is target


@pytest.mark.parametrize(
    ("status", "event"),
    [
        (status, event)
        for status in OrderStatus
        for event in ORDER_EVENT_KINDS
        if event not in ORDER_TRANSITIONS[status]
    ],
)
def test_every_pair_absent_from_the_table_is_refused(
    status: OrderStatus, event: ExecutionEventKind
) -> None:
    assert next_order_status(status, event, ZERO) is None
    assert next_order_status(status, event, SOME) is None


def test_fills_are_only_legal_once_the_order_is_working_at_the_venue() -> None:
    for status in (S.NEW, S.PENDING):
        for event in FILL_EVENT_KINDS:
            assert next_order_status(status, event, ZERO) is None
    for status in (S.ACCEPTED, S.PARTIALLY_FILLED):
        assert next_order_status(status, E.ORDER_PARTIALLY_FILLED, ZERO) is S.PARTIALLY_FILLED
    for status in (S.ACCEPTED, S.PARTIALLY_FILLED, S.CANCEL_PENDING):
        assert next_order_status(status, E.ORDER_FILLED, ZERO) is S.FILLED
    # A partial fill does not answer a pending cancel.
    assert next_order_status(S.CANCEL_PENDING, E.ORDER_PARTIALLY_FILLED, ZERO) is S.CANCEL_PENDING


def test_a_rejection_is_refused_for_an_order_that_has_traded() -> None:
    assert next_order_status(S.ACCEPTED, E.ORDER_REJECTED, ZERO) is S.REJECTED
    assert next_order_status(S.CANCEL_PENDING, E.ORDER_REJECTED, ZERO) is S.REJECTED
    assert next_order_status(S.CANCEL_PENDING, E.ORDER_REJECTED, SOME) is None
    assert next_order_status(S.PARTIALLY_FILLED, E.ORDER_REJECTED, ZERO) is None


def test_a_refused_cancel_returns_to_the_working_status_its_fills_imply() -> None:
    assert working_status_for(ZERO) is S.ACCEPTED
    assert working_status_for(SOME) is S.PARTIALLY_FILLED
    assert next_order_status(S.CANCEL_PENDING, E.ORDER_CANCEL_REJECTED, ZERO) is S.ACCEPTED
    assert next_order_status(S.CANCEL_PENDING, E.ORDER_CANCEL_REJECTED, SOME) is S.PARTIALLY_FILLED
    for status in OrderStatus:
        if status is not S.CANCEL_PENDING:
            assert next_order_status(status, E.ORDER_CANCEL_REJECTED, ZERO) is None


def test_an_amendment_keeps_the_status_it_finds_and_finished_orders_take_none() -> None:
    for status in WORKING_ORDER_STATUSES:
        assert next_order_status(status, E.ORDER_REPLACED, ZERO) is status
    for status in TERMINAL_ORDER_STATUSES:
        assert next_order_status(status, E.ORDER_REPLACED, ZERO) is None


# --------------------------------------------------------------------------- #
# Reachability, and the one intended cycle
# --------------------------------------------------------------------------- #


def test_terminal_statuses_reach_nothing() -> None:
    for status in TERMINAL_ORDER_STATUSES:
        assert reachable_statuses(status) == frozenset()


def test_every_working_status_can_still_finish() -> None:
    for status in WORKING_ORDER_STATUSES:
        assert reachable_statuses(status) & TERMINAL_ORDER_STATUSES


def test_the_graph_is_acyclic_but_for_self_loops_and_the_refused_cancel() -> None:
    """Amendments and repeated partial fills loop on one status -- a partial fill
    while a cancel is pending too -- and a refused cancel returns
    ``CANCEL_PENDING`` to working. Remove exactly those and what remains must be
    a DAG: nothing else may ever lead an order back."""

    edges: dict[OrderStatus, set[OrderStatus]] = {status: set() for status in OrderStatus}
    for status, row in ORDER_TRANSITIONS.items():
        for event, target in row.items():
            if target is status or event is E.ORDER_CANCEL_REJECTED:
                continue
            edges[status].add(target)
    for status in CANCEL_REQUESTABLE_STATUSES:
        edges[status].add(S.CANCEL_PENDING)

    visiting: set[OrderStatus] = set()
    done: set[OrderStatus] = set()

    def visit(node: OrderStatus) -> None:
        assert node not in visiting, f"a cycle through {node}"
        if node in done:
            return
        visiting.add(node)
        for successor in edges[node]:
            visit(successor)
        visiting.discard(node)
        done.add(node)

    for status in OrderStatus:
        visit(status)


# --------------------------------------------------------------------------- #
# Classification: total, and exactly one answer each
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("status", "event", "filled"),
    list(itertools.product(OrderStatus, sorted(STATUS_EVENT_KINDS), (ZERO, SOME))),
)
def test_classification_is_total_and_consistent_with_the_table(
    status: OrderStatus, event: ExecutionEventKind, filled: Decimal
) -> None:
    classification, reported = classify_order_event(status, event, filled)
    legal = next_order_status(status, event, filled)
    if legal is not None:
        assert classification is EventClassification.TRANSITION
        assert reported is legal
    elif reported is status:
        assert classification is EventClassification.DUPLICATE
    elif status in reachable_statuses(reported):
        assert classification is EventClassification.STALE
    else:
        assert classification is EventClassification.CONFLICT


@pytest.mark.parametrize(
    ("status", "event", "filled", "expected"),
    [
        (S.ACCEPTED, E.ORDER_ACCEPTED, ZERO, EventClassification.DUPLICATE),
        (S.PARTIALLY_FILLED, E.ORDER_ACCEPTED, SOME, EventClassification.STALE),
        (S.FILLED, E.ORDER_ACCEPTED, SOME, EventClassification.STALE),
        (S.CANCEL_PENDING, E.ORDER_ACCEPTED, ZERO, EventClassification.STALE),
        (S.REJECTED, E.ORDER_REJECTED, ZERO, EventClassification.DUPLICATE),
        (S.CANCELLED, E.ORDER_CANCELLED, ZERO, EventClassification.DUPLICATE),
        (S.EXPIRED, E.ORDER_EXPIRED, ZERO, EventClassification.DUPLICATE),
        (S.FILLED, E.ORDER_CANCELLED, SOME, EventClassification.CONFLICT),
        (S.EXPIRED, E.ORDER_CANCELLED, ZERO, EventClassification.CONFLICT),
        (S.CANCELLED, E.ORDER_REJECTED, ZERO, EventClassification.CONFLICT),
        (S.PARTIALLY_FILLED, E.ORDER_REJECTED, SOME, EventClassification.CONFLICT),
        (S.FILLED, E.ORDER_CANCEL_REJECTED, SOME, EventClassification.STALE),
        (S.CANCEL_PENDING, E.ORDER_CANCEL_REJECTED, SOME, EventClassification.TRANSITION),
    ],
)
def test_the_cases_a_venue_actually_produces(
    status: OrderStatus,
    event: ExecutionEventKind,
    filled: Decimal,
    expected: EventClassification,
) -> None:
    assert classify_order_event(status, event, filled)[0] is expected


def test_fills_and_amendments_are_not_classified_by_status() -> None:
    for event in (*FILL_EVENT_KINDS, E.ORDER_REPLACED, E.POSITION_CHANGED):
        with pytest.raises(ValueError, match="not a status-changing event"):
            classify_order_event(S.ACCEPTED, event, ZERO)


# --------------------------------------------------------------------------- #
# The OMS reads the table
# --------------------------------------------------------------------------- #


def _order(status: OrderStatus, quantity: str = "10", filled: str = "0") -> Order:
    q, f = Decimal(quantity), Decimal(filled)
    return Order(
        order_id=OrderId(uuid4()),
        strategy_id="S",
        asset_id="A",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        status=status,
        quantity=q,
        filled_quantity=f,
        remaining_quantity=q - f,
        limit_price=Decimal("100"),
        stop_price=None,
        average_fill_price=Decimal("100") if f else Decimal("0"),
        created_at=1.0,
        updated_at=1.0,
    )


_METHODS: dict[ExecutionEventKind, Callable[[Order], Order]] = {
    E.ORDER_ACCEPTED: lambda order: order.accept(2.0),
    E.ORDER_REJECTED: lambda order: order.reject(2.0),
    E.ORDER_CANCELLED: lambda order: order.cancel(2.0),
    E.ORDER_EXPIRED: lambda order: order.expire(2.0),
    E.ORDER_PARTIALLY_FILLED: lambda order: order.partial_fill(Decimal("1"), Decimal("99"), 2.0),
    E.ORDER_FILLED: lambda order: order.fill(order.remaining_quantity, Decimal("99"), 2.0),
    E.ORDER_REPLACED: lambda order: order.replace(order.quantity + 5, 2.0),
}


def _possible(status: OrderStatus, filled: str) -> bool:
    """Whether an order can be in ``status`` with ``filled`` traded.

    Nothing trades before an order is working and a partially filled order has
    traded; every other pairing occurs.
    """

    if status in {S.NEW, S.PENDING, S.ACCEPTED}:
        return filled == "0"
    if status is S.PARTIALLY_FILLED:
        return filled != "0"
    return True


@pytest.mark.parametrize(
    ("status", "event", "filled"),
    [
        (status, event, filled)
        for status, event, filled in itertools.product(OrderStatus, sorted(_METHODS), ("0", "3"))
        if _possible(status, filled)
    ],
)
def test_the_oms_permits_exactly_what_the_table_permits(
    status: OrderStatus, event: ExecutionEventKind, filled: str
) -> None:
    order = _order(status, filled=filled)
    target = next_order_status(status, event, Decimal(filled))
    if target is None:
        with pytest.raises(InvalidTransitionError):
            _METHODS[event](order)
    else:
        assert _METHODS[event](order).status is target


def test_a_fill_while_a_cancel_is_pending_lands_and_the_cancel_stays_pending() -> None:
    pending = _order(S.CANCEL_PENDING)
    partly = pending.partial_fill(Decimal("4"), Decimal("99"), 2.0)
    assert partly.status is S.CANCEL_PENDING
    assert partly.filled_quantity == 4
    assert pending.fill(Decimal("10"), Decimal("99"), 2.0).status is S.FILLED


def test_a_partial_fill_must_leave_quantity_working() -> None:
    accepted = _order(S.ACCEPTED)
    with pytest.raises(InvalidTransitionError, match="leaves nothing working"):
        accepted.partial_fill(Decimal("10"), Decimal("99"), 2.0)
    with pytest.raises(InvalidTransitionError, match="leaves nothing working"):
        accepted.partial_fill(Decimal("11"), Decimal("99"), 2.0)


def test_a_complete_fill_must_complete_the_order_exactly() -> None:
    accepted = _order(S.ACCEPTED)
    with pytest.raises(InvalidTransitionError, match="executes exactly what is working"):
        accepted.fill(Decimal("9"), Decimal("99"), 2.0)
    with pytest.raises(InvalidTransitionError, match="executes exactly what is working"):
        accepted.fill(Decimal("11"), Decimal("99"), 2.0)
    done = accepted.fill(Decimal("10"), Decimal("99"), 2.0)
    assert done.remaining_quantity == 0 and done.filled_quantity == done.quantity


@pytest.mark.parametrize("quantity", ["0", "-1"])
def test_a_fill_of_nothing_is_not_a_fill(quantity: str) -> None:
    with pytest.raises(InvalidTransitionError, match="not a fill"):
        _order(S.ACCEPTED).partial_fill(Decimal(quantity), Decimal("99"), 2.0)


def test_an_amendment_must_leave_something_working() -> None:
    traded = _order(S.PARTIALLY_FILLED, filled="3")
    with pytest.raises(InvalidTransitionError, match="nothing would be left"):
        traded.replace(Decimal("3"), 2.0)
    with pytest.raises(InvalidTransitionError, match="nothing would be left"):
        traded.replace(Decimal("2"), 2.0)
    amended = traded.replace(Decimal("4"), 2.0, new_limit=Decimal("101"))
    assert amended.remaining_quantity == 1
    assert amended.limit_price == Decimal("101")
    assert amended.status is S.PARTIALLY_FILLED


def test_the_existing_refusal_messages_are_unchanged() -> None:
    """Callers match on these; the table changed who decides, not what is said."""

    filled = _order(S.FILLED, filled="10")
    with pytest.raises(InvalidTransitionError, match="Cannot accept order in status"):
        filled.accept(2.0)
    with pytest.raises(InvalidTransitionError, match="Cannot reject order in status"):
        filled.reject(2.0)
    with pytest.raises(InvalidTransitionError, match="Cannot cancel a closed order"):
        filled.cancel(2.0)
    with pytest.raises(InvalidTransitionError, match="Cannot expire a closed order"):
        filled.expire(2.0)
    with pytest.raises(InvalidTransitionError, match="Cannot partially fill order in status"):
        filled.partial_fill(Decimal("1"), Decimal("1"), 2.0)
    with pytest.raises(InvalidTransitionError, match="Cannot fill order in status"):
        filled.fill(Decimal("1"), Decimal("1"), 2.0)
    with pytest.raises(InvalidTransitionError, match="Cannot replace closed order"):
        filled.replace(Decimal("20"), 2.0)
