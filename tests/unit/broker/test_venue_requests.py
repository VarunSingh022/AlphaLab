"""Cancel and modify requests: a retry is recognised, a new request is not mistaken for one."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from alphalab.broker import BrokerEngine, PaperBroker
from alphalab.broker.adapter import BrokerAdapter
from alphalab.broker.exceptions import BrokerValidationError, InvalidBrokerStateError
from alphalab.broker.lifecycle import VenueEvent, apply_venue_event
from alphalab.broker.order import BrokerOrderStatus
from alphalab.broker.requests import (
    CancelRequest,
    ModifyRequest,
    RequestLedger,
    RequestOutcome,
    issue_cancel,
    issue_modify,
)
from alphalab.broker.validation import validate_cancel_request, validate_replace_request
from alphalab.core.enums import OrderStatus, OrderType
from alphalab.core.lifecycle import ExecutionEventKind
from tests.unit.broker.venue_fixtures import execution, mirror, order

E = ExecutionEventKind
NEW, DUPLICATE, REFUSED = RequestOutcome.NEW, RequestOutcome.DUPLICATE, RequestOutcome.REFUSED


def _modify(revision: int = 1, quantity: str = "120", price: str = "151") -> ModifyRequest:
    return ModifyRequest("B-1", revision, Decimal(quantity), Decimal(price), 40.0)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_a_retry_has_the_same_identity_whenever_it_is_sent() -> None:
    assert CancelRequest("B-1", 1, 1.0).request_id == CancelRequest("B-1", 1, 99.0).request_id
    assert (
        ModifyRequest("B-1", 1, Decimal("5"), Decimal("1"), 1.0).request_id
        == ModifyRequest("B-1", 1, Decimal("5"), Decimal("1"), 99.0).request_id
    )


def test_a_new_request_has_a_new_identity() -> None:
    base = CancelRequest("B-1", 1, 1.0).request_id
    assert CancelRequest("B-1", 2, 1.0).request_id != base
    assert CancelRequest("B-2", 1, 1.0).request_id != base
    first = _modify().request_id
    assert _modify(revision=2).request_id != first
    assert _modify(quantity="121").request_id != first
    assert _modify(price="152").request_id != first
    assert first != base, "a cancel and a modify never share an identity"


@pytest.mark.parametrize(
    "build",
    [
        lambda: CancelRequest("", 1, 1.0),
        lambda: CancelRequest("B-1", 0, 1.0),
        lambda: CancelRequest("B-1", 1, float("inf")),
        lambda: ModifyRequest("B-1", 0, Decimal("1"), Decimal("1"), 1.0),
        lambda: ModifyRequest("B-1", 1, Decimal("0"), Decimal("1"), 1.0),
        lambda: ModifyRequest("B-1", 1, Decimal("1"), Decimal("-1"), 1.0),
    ],
)
def test_a_malformed_request_is_refused_at_construction(build: Callable[[], object]) -> None:
    with pytest.raises(BrokerValidationError):
        build()


# --------------------------------------------------------------------------- #
# Cancels
# --------------------------------------------------------------------------- #


def test_a_new_cancel_marks_the_order_pending_and_a_retry_is_a_duplicate() -> None:
    state, ledger = mirror(order()), RequestLedger()
    state, ledger, first = issue_cancel(state, ledger, CancelRequest("B-1", 1, 40.0))
    assert first.outcome is NEW and first.send
    assert state.orders["B-1"].status is BrokerOrderStatus.PENDING_CANCEL
    assert state.orders["B-1"].updated_at == 40.0

    retried_state, retried_ledger, retry = issue_cancel(
        state, ledger, CancelRequest("B-1", 1, 45.0)
    )
    assert retry.outcome is DUPLICATE and not retry.send
    assert (retried_state, retried_ledger) == (state, ledger)


def test_a_second_cancel_while_one_is_in_flight_is_not_sent() -> None:
    state, ledger, _ = issue_cancel(mirror(order()), RequestLedger(), CancelRequest("B-1", 1, 40.0))
    _, _, second = issue_cancel(state, ledger, CancelRequest("B-1", 2, 41.0))
    assert second.outcome is DUPLICATE
    assert "already in flight" in second.reason


def test_after_a_refused_cancel_the_next_attempt_is_new() -> None:
    state, ledger, _ = issue_cancel(mirror(order()), RequestLedger(), CancelRequest("B-1", 1, 40.0))
    state, refused = apply_venue_event(
        state, VenueEvent(E.ORDER_CANCEL_REJECTED, 41.0, "B-1", reason="too late")
    )
    assert refused.applied and state.orders["B-1"].status is OrderStatus.ACCEPTED

    state, ledger, again = issue_cancel(state, ledger, CancelRequest("B-1", 2, 42.0))
    assert again.outcome is NEW
    _, _, older = issue_cancel(state, ledger, CancelRequest("B-1", 1, 43.0))
    assert older.outcome is DUPLICATE, "attempt 1 was issued; retrying it is a retry"


def test_an_attempt_that_predates_the_latest_is_refused() -> None:
    state, ledger, _ = issue_cancel(mirror(order()), RequestLedger(), CancelRequest("B-1", 3, 40.0))
    state, _ = apply_venue_event(state, VenueEvent(E.ORDER_CANCEL_REJECTED, 41.0, "B-1"))
    _, _, stale = issue_cancel(state, ledger, CancelRequest("B-1", 2, 42.0))
    assert stale.outcome is REFUSED
    assert "predates" in stale.reason


@pytest.mark.parametrize(
    "status", [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED]
)
def test_a_finished_order_has_nothing_to_cancel(status: OrderStatus) -> None:
    filled = "100" if status is OrderStatus.FILLED else "0"
    state = mirror(order(status=status, filled=filled))
    after, ledger, decision = issue_cancel(state, RequestLedger(), CancelRequest("B-1", 1, 40.0))
    assert decision.outcome is REFUSED
    assert after is state and ledger == RequestLedger()


def test_an_unknown_order_cannot_be_cancelled() -> None:
    _, _, decision = issue_cancel(mirror(), RequestLedger(), CancelRequest("B-1", 1, 40.0))
    assert decision.outcome is REFUSED


# --------------------------------------------------------------------------- #
# Modifications
# --------------------------------------------------------------------------- #


def test_a_new_amendment_is_recorded_and_leaves_the_mirror_alone() -> None:
    state = mirror(order())
    after, ledger, decision = issue_modify(state, RequestLedger(), _modify())
    assert decision.outcome is NEW
    assert after is state, "the venue's confirmation changes the mirror, not the request"
    _, _, retry = issue_modify(after, ledger, _modify())
    assert retry.outcome is DUPLICATE


def test_amending_there_and_back_is_three_requests() -> None:
    state, ledger = mirror(order()), RequestLedger()
    for revision, price in ((1, "101"), (2, "102"), (3, "101")):
        state, ledger, decision = issue_modify(state, ledger, _modify(revision, price=price))
        assert decision.outcome is NEW, (revision, price)
        state, _ = apply_venue_event(
            state,
            VenueEvent(
                E.ORDER_REPLACED,
                40.0 + revision,
                "B-1",
                quantity=Decimal("120"),
                price=Decimal(price),
            ),
        )
    assert state.orders["B-1"].price == Decimal("101")


def test_a_revision_reused_for_different_content_is_refused() -> None:
    _, ledger, _ = issue_modify(mirror(order()), RequestLedger(), _modify(1, price="101"))
    _, _, reused = issue_modify(mirror(order()), ledger, _modify(1, price="102"))
    assert reused.outcome is REFUSED
    assert "already issued" in reused.reason


def test_a_revision_older_than_the_latest_is_refused() -> None:
    _, ledger, _ = issue_modify(mirror(order()), RequestLedger(), _modify(5))
    _, _, older = issue_modify(mirror(order()), ledger, _modify(4, price="160"))
    assert older.outcome is REFUSED
    assert "predates" in older.reason


@pytest.mark.parametrize(
    ("status", "filled", "amendment", "reason"),
    [
        (OrderStatus.FILLED, "100", _modify(), "finished"),
        (BrokerOrderStatus.PENDING_CANCEL, "0", _modify(), "cancel in flight"),
        (OrderStatus.PARTIALLY_FILLED, "40", _modify(quantity="40"), "leaves nothing working"),
        (OrderStatus.ACCEPTED, "0", _modify(quantity="100", price="150"), "changes nothing"),
    ],
)
def test_an_amendment_that_cannot_be_sent_is_refused(
    status: OrderStatus | BrokerOrderStatus, filled: str, amendment: ModifyRequest, reason: str
) -> None:
    _, _, decision = issue_modify(
        mirror(order(status=status, filled=filled)), RequestLedger(), amendment
    )
    assert decision.outcome is REFUSED
    assert reason in decision.reason


# --------------------------------------------------------------------------- #
# The two validation defects v3.9 closed
# --------------------------------------------------------------------------- #


def test_an_expired_order_can_no_longer_be_cancelled() -> None:
    """``validate_cancel_request`` listed three terminal statuses and missed EXPIRED."""

    state = mirror(order(status=OrderStatus.EXPIRED))
    with pytest.raises(InvalidBrokerStateError):
        validate_cancel_request(state, "B-1")
    with pytest.raises(InvalidBrokerStateError):
        PaperBroker().cancel_order(state, "B-1", 2.0)


def test_the_reference_adapter_refuses_an_amendment_below_what_has_filled() -> None:
    broker = PaperBroker()
    state = BrokerEngine.initialize("PAPER", Decimal("100000"), "USD")
    state, _ = broker.connect(state, 1.0)

    class _Resting:
        order_id = "OMS-1"
        asset_id = "AAPL"
        side = "BUY"
        quantity = Decimal("100")
        price = Decimal("150")

    resting = BrokerAdapter.to_broker_order(_Resting(), "B-1", OrderType.LIMIT, 2.0)
    state, _ = broker.submit_order(state, resting, 2.0)
    state, _ = apply_venue_event(
        state,
        VenueEvent(
            E.ORDER_PARTIALLY_FILLED,
            3.0,
            "B-1",
            execution=execution("X-1", "B-1", "40"),
        ),
    )
    with pytest.raises(BrokerValidationError, match="nothing would be left working"):
        broker.replace_order(state, "B-1", Decimal("40"), Decimal("150"), 4.0)
    with pytest.raises(BrokerValidationError, match="negative"):
        validate_replace_request(state, "B-1", Decimal("120"), Decimal("-1"))
    amended, _ = broker.replace_order(state, "B-1", Decimal("120"), Decimal("149"), 4.0)
    assert amended.orders["B-1"].quantity == 120
