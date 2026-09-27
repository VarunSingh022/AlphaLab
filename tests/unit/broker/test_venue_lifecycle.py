"""Normalized venue events applied to the mirror: every outcome, every race, idempotency."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from decimal import Decimal

import pytest

from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.lifecycle import (
    LifecycleOutcome,
    VenueEvent,
    apply_venue_event,
    apply_venue_events,
    classify_venue_event,
)
from alphalab.broker.order import BrokerOrderStatus
from alphalab.broker.reconciliation import ExecutionOutcome
from alphalab.broker.state import ConnectionStatus
from alphalab.core.enums import OrderStatus
from alphalab.core.lifecycle import ExecutionEventKind
from tests.unit.broker.venue_fixtures import (
    account,
    event,
    execution,
    fill,
    mirror,
    order,
    position,
)

E = ExecutionEventKind
S = OrderStatus
R = LifecycleOutcome


# --------------------------------------------------------------------------- #
# Construction: the payload must match the kind
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: VenueEvent(E.ORDER_FILLED, 1.0, "B-1"),
        lambda: VenueEvent(E.ORDER_REPLACED, 1.0, "B-1", quantity=Decimal("5")),
        lambda: VenueEvent(E.ORDER_ACCEPTED, 1.0, "B-1", quantity=Decimal("5")),
        lambda: VenueEvent(E.ORDER_ACCEPTED, 1.0),
        lambda: VenueEvent(E.POSITION_CHANGED, 1.0),
        lambda: VenueEvent(E.POSITION_CHANGED, 1.0, "B-1", position=position()),
        lambda: VenueEvent(E.BALANCE_CHANGED, 1.0),
        lambda: VenueEvent(E.BROKER_DISCONNECTED, 1.0, account=account()),
        lambda: VenueEvent(E.ORDER_ACCEPTED, float("nan"), "B-1"),
        lambda: VenueEvent(E.ORDER_FILLED, 1.0, "B-2", execution=execution(broker_order_id="B-1")),
    ],
)
def test_an_event_whose_payload_does_not_match_its_kind_is_refused(
    build: Callable[[], VenueEvent],
) -> None:
    with pytest.raises(BrokerValidationError):
        build()


# --------------------------------------------------------------------------- #
# Status events
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("status", "kind", "outcome", "after"),
    [
        (BrokerOrderStatus.SUBMITTED, E.ORDER_ACCEPTED, R.APPLIED, S.ACCEPTED),
        (BrokerOrderStatus.PENDING_SUBMIT, E.ORDER_ACCEPTED, R.APPLIED, S.ACCEPTED),
        (S.ACCEPTED, E.ORDER_ACCEPTED, R.DUPLICATE, S.ACCEPTED),
        (BrokerOrderStatus.SUBMITTED, E.ORDER_REJECTED, R.APPLIED, S.REJECTED),
        (S.ACCEPTED, E.ORDER_REJECTED, R.APPLIED, S.REJECTED),
        (S.REJECTED, E.ORDER_REJECTED, R.DUPLICATE, S.REJECTED),
        (S.REJECTED, E.ORDER_ACCEPTED, R.STALE, S.REJECTED),
        (S.ACCEPTED, E.ORDER_CANCELLED, R.APPLIED, S.CANCELLED),
        (BrokerOrderStatus.PENDING_CANCEL, E.ORDER_CANCELLED, R.APPLIED, S.CANCELLED),
        (S.CANCELLED, E.ORDER_CANCELLED, R.DUPLICATE, S.CANCELLED),
        (S.ACCEPTED, E.ORDER_EXPIRED, R.APPLIED, S.EXPIRED),
        (S.EXPIRED, E.ORDER_EXPIRED, R.DUPLICATE, S.EXPIRED),
        (S.CANCELLED, E.ORDER_EXPIRED, R.CONFLICT, S.CANCELLED),
        (S.EXPIRED, E.ORDER_CANCELLED, R.CONFLICT, S.EXPIRED),
        (S.CANCELLED, E.ORDER_REJECTED, R.CONFLICT, S.CANCELLED),
        (BrokerOrderStatus.PENDING_CANCEL, E.ORDER_ACCEPTED, R.STALE, None),
        (BrokerOrderStatus.PENDING_CANCEL, E.ORDER_CANCEL_REJECTED, R.APPLIED, S.ACCEPTED),
        (S.ACCEPTED, E.ORDER_CANCEL_REJECTED, R.DUPLICATE, S.ACCEPTED),
    ],
)
def test_status_events_on_an_untraded_order(
    status: OrderStatus | BrokerOrderStatus,
    kind: ExecutionEventKind,
    outcome: LifecycleOutcome,
    after: OrderStatus | None,
) -> None:
    state = mirror(order(status=status))
    new, decision = apply_venue_event(state, event(kind))

    assert decision.outcome is outcome
    assert decision.status_before == status
    expected = status if after is None else after
    assert new.orders["B-1"].status == expected
    if outcome is not R.APPLIED:
        assert new is state, "a refused or redundant event must not rebuild the state"


@pytest.mark.parametrize(
    ("status", "kind", "outcome"),
    [
        (S.PARTIALLY_FILLED, E.ORDER_ACCEPTED, R.STALE),
        (S.FILLED, E.ORDER_ACCEPTED, R.STALE),
        (S.PARTIALLY_FILLED, E.ORDER_REJECTED, R.CONFLICT),
        (S.FILLED, E.ORDER_REJECTED, R.CONFLICT),
        (S.FILLED, E.ORDER_CANCELLED, R.CONFLICT),
        (S.FILLED, E.ORDER_EXPIRED, R.CONFLICT),
        (S.FILLED, E.ORDER_CANCEL_REJECTED, R.STALE),
        (BrokerOrderStatus.PENDING_CANCEL, E.ORDER_REJECTED, R.CONFLICT),
    ],
)
def test_status_events_on_an_order_that_has_traded(
    status: OrderStatus | BrokerOrderStatus, kind: ExecutionEventKind, outcome: LifecycleOutcome
) -> None:
    quantity = "100"
    filled = "100" if status is S.FILLED else "40"
    state = mirror(order(status=status, filled=filled, quantity=quantity))
    new, decision = apply_venue_event(state, event(kind))
    assert decision.outcome is outcome
    assert new is state


def test_a_refused_cancel_on_a_traded_order_returns_it_to_partially_filled() -> None:
    state = mirror(order(status=BrokerOrderStatus.PENDING_CANCEL, filled="40"))
    new, decision = apply_venue_event(state, event(E.ORDER_CANCEL_REJECTED))
    assert decision.applied
    assert new.orders["B-1"].status is S.PARTIALLY_FILLED


def test_an_applied_event_moves_updated_at_forward_and_never_back() -> None:
    state = mirror(order(status=BrokerOrderStatus.SUBMITTED, updated_at=50.0))
    later, _ = apply_venue_event(state, event(E.ORDER_ACCEPTED, at=60.0))
    earlier, _ = apply_venue_event(state, event(E.ORDER_ACCEPTED, at=40.0))
    assert later.orders["B-1"].updated_at == 60.0
    assert earlier.orders["B-1"].updated_at == 50.0


def test_an_event_for_an_order_the_mirror_never_held_is_a_break() -> None:
    state = mirror(order())
    for kind in (E.ORDER_ACCEPTED, E.ORDER_CANCELLED, E.ORDER_EXPIRED):
        decision = classify_venue_event(state, event(kind, broker_order_id="B-GHOST"))
        assert decision.outcome is R.UNKNOWN_ORDER
        assert decision.is_break


# --------------------------------------------------------------------------- #
# Fills
# --------------------------------------------------------------------------- #


def test_a_partial_then_a_completing_fill_apply_through_the_existing_authority() -> None:
    state = mirror(order())
    state, first = apply_venue_event(state, fill("X-1", "40"))
    state, second = apply_venue_event(state, fill("X-2", "60", complete=True))

    assert first.applied and second.applied
    assert first.execution is not None and first.execution.outcome is ExecutionOutcome.APPLIED
    assert state.orders["B-1"].status is S.FILLED
    assert state.orders["B-1"].filled_quantity == 100
    assert set(state.executions) == {"X-1", "X-2"}


def test_a_redelivered_fill_is_a_duplicate_and_changes_nothing() -> None:
    state, _ = apply_venue_event(mirror(order()), fill("X-1", "40"))
    again, decision = apply_venue_event(state, fill("X-1", "40"))
    assert decision.outcome is R.DUPLICATE
    assert not decision.is_break
    assert again is state


@pytest.mark.parametrize(
    ("status", "filled", "quantity", "complete", "outcome"),
    [
        (S.CANCELLED, "0", "40", False, R.CONFLICT),
        (S.ACCEPTED, "80", "40", False, R.CONFLICT),
        (S.ACCEPTED, "0", "0", False, R.INVALID),
    ],
)
def test_fills_that_cannot_be_applied_are_classified_not_applied(
    status: OrderStatus, filled: str, quantity: str, complete: bool, outcome: LifecycleOutcome
) -> None:
    state = mirror(order(status=status, filled=filled))
    new, decision = apply_venue_event(state, fill("X-9", quantity, complete=complete))
    assert decision.outcome is outcome
    assert decision.is_break
    assert new is state


def test_a_fills_name_is_recorded_and_its_quantity_decides() -> None:
    """A venue names a fill by its own cumulative state; the mirror applies quantities."""

    state = mirror(order(filled="60"))
    after_partial_name, called_partial = apply_venue_event(state, fill("X-1", "40"))
    after_complete_name, called_complete = apply_venue_event(
        state, fill("X-1", "40", complete=True)
    )
    assert called_partial.applied and called_complete.applied
    assert "The quantities decide" in called_partial.reason
    assert called_complete.reason == ""
    assert after_partial_name.orders["B-1"] == after_complete_name.orders["B-1"]
    assert after_partial_name.orders["B-1"].status is S.FILLED

    early, named_complete = apply_venue_event(mirror(order()), fill("X-2", "60", complete=True))
    assert named_complete.applied and not named_complete.is_break
    assert early.orders["B-1"].status is S.PARTIALLY_FILLED


def test_a_fill_before_the_acknowledgement_implies_it_and_the_late_ack_is_stale() -> None:
    state = mirror(order(status=BrokerOrderStatus.SUBMITTED))
    state, filled = apply_venue_event(state, fill("X-1", "40"))
    assert filled.applied
    assert filled.implied == (E.ORDER_ACCEPTED,)
    assert state.orders["B-1"].status is S.PARTIALLY_FILLED

    state2, ack = apply_venue_event(state, event(E.ORDER_ACCEPTED))
    assert ack.outcome is R.STALE
    assert state2 is state


def test_a_partial_fill_during_a_pending_cancel_keeps_the_cancel_pending() -> None:
    state = mirror(order(status=BrokerOrderStatus.PENDING_CANCEL))
    state, decision = apply_venue_event(state, fill("X-1", "40"))
    assert decision.applied
    assert state.orders["B-1"].status is BrokerOrderStatus.PENDING_CANCEL
    state, done = apply_venue_event(state, fill("X-2", "60", complete=True))
    assert done.applied
    assert state.orders["B-1"].status is S.FILLED
    _, late_cancel = apply_venue_event(state, event(E.ORDER_CANCELLED))
    assert late_cancel.outcome is R.CONFLICT


# --------------------------------------------------------------------------- #
# Amendments
# --------------------------------------------------------------------------- #


def _replace(quantity: str, price: str, at: float = 30.0) -> VenueEvent:
    return VenueEvent(E.ORDER_REPLACED, at, "B-1", quantity=Decimal(quantity), price=Decimal(price))


def test_an_amendment_changes_quantity_and_price_and_keeps_the_status() -> None:
    state = mirror(order(status=S.PARTIALLY_FILLED, filled="40"))
    new, decision = apply_venue_event(state, _replace("150", "149"))
    assert decision.applied
    amended = new.orders["B-1"]
    assert (amended.quantity, amended.price, amended.status) == (
        Decimal("150"),
        Decimal("149"),
        S.PARTIALLY_FILLED,
    )


@pytest.mark.parametrize(
    ("status", "filled", "quantity", "price", "outcome"),
    [
        (S.ACCEPTED, "0", "100", "150", R.DUPLICATE),
        (S.ACCEPTED, "0", "0", "150", R.INVALID),
        (S.ACCEPTED, "0", "100", "-1", R.INVALID),
        (S.PARTIALLY_FILLED, "40", "40", "150", R.CONFLICT),
        (S.FILLED, "100", "120", "150", R.CONFLICT),
        (BrokerOrderStatus.PENDING_CANCEL, "0", "120", "150", R.APPLIED),
    ],
)
def test_amendments_are_classified(
    status: OrderStatus | BrokerOrderStatus,
    filled: str,
    quantity: str,
    price: str,
    outcome: LifecycleOutcome,
) -> None:
    decision = classify_venue_event(
        mirror(order(status=status, filled=filled)), _replace(quantity, price)
    )
    assert decision.outcome is outcome


# --------------------------------------------------------------------------- #
# Positions, balances, connectivity
# --------------------------------------------------------------------------- #


def test_positions_and_balances_are_absolute_and_scoped_to_the_account() -> None:
    state = mirror(order())
    held = VenueEvent(E.POSITION_CHANGED, 1.0, position=position(quantity="40"))
    state, applied = apply_venue_event(state, held)
    assert applied.applied and state.positions["AAPL"].quantity == 40
    assert classify_venue_event(state, held).outcome is R.DUPLICATE

    foreign = VenueEvent(E.POSITION_CHANGED, 1.0, position=position(account_id="OTHER"))
    assert classify_venue_event(state, foreign).outcome is R.INVALID

    richer = VenueEvent(E.BALANCE_CHANGED, 1.0, account=account(cash="90000"))
    state, balance = apply_venue_event(state, richer)
    assert balance.applied and state.account.cash == 90000
    assert classify_venue_event(state, richer).outcome is R.DUPLICATE

    other_currency = VenueEvent(E.BALANCE_CHANGED, 1.0, account=account(currency="EUR"))
    assert classify_venue_event(state, other_currency).outcome is R.INVALID
    other_account = VenueEvent(E.BALANCE_CHANGED, 1.0, account=account(account_id="ACC-2"))
    assert classify_venue_event(state, other_account).outcome is R.INVALID


def test_a_disconnect_stops_trading_and_a_connect_demands_a_resync() -> None:
    state = mirror(order())
    down, dropped = apply_venue_event(state, VenueEvent(E.BROKER_DISCONNECTED, 1.0, reason="EOF"))
    assert dropped.applied
    assert "EOF" in dropped.reason
    assert not down.connection_status.can_trade
    assert not dropped.resync_required
    assert classify_venue_event(down, VenueEvent(E.BROKER_DISCONNECTED, 2.0)).outcome is (
        R.DUPLICATE
    )

    up, restored = apply_venue_event(down, VenueEvent(E.BROKER_CONNECTED, 3.0))
    assert restored.applied
    assert restored.resync_required
    assert up.connection_status is ConnectionStatus.CONNECTED
    again = classify_venue_event(up, VenueEvent(E.BROKER_CONNECTED, 4.0))
    assert again.outcome is R.DUPLICATE
    assert not again.resync_required


# --------------------------------------------------------------------------- #
# Ordering and idempotency, over whole streams
# --------------------------------------------------------------------------- #


def test_status_events_in_any_delivery_order_end_in_the_same_place() -> None:
    stream = [event(E.ORDER_ACCEPTED), fill("X-1", "40"), fill("X-2", "60", complete=True)]
    endings = set()
    for permutation in itertools.permutations(stream):
        final, _ = apply_venue_events(
            mirror(order(status=BrokerOrderStatus.SUBMITTED)), permutation
        )
        endings.add((final.orders["B-1"].status, final.orders["B-1"].filled_quantity))
    assert endings == {(S.FILLED, Decimal("100"))}


def test_replaying_a_stream_is_a_no_op() -> None:
    stream = [
        event(E.ORDER_ACCEPTED),
        fill("X-1", "40"),
        VenueEvent(E.POSITION_CHANGED, 21.0, position=position(quantity="40")),
        event(E.ORDER_CANCELLED, at=25.0),
    ]
    first, decisions = apply_venue_events(mirror(order(status=BrokerOrderStatus.SUBMITTED)), stream)
    assert all(d.applied for d in decisions)
    second, replayed = apply_venue_events(first, stream)
    assert second is first
    assert {d.outcome for d in replayed} <= {R.DUPLICATE, R.STALE}
    assert not any(d.is_break for d in replayed)


def test_classification_never_changes_the_state_it_reads() -> None:
    state = mirror(order(status=BrokerOrderStatus.SUBMITTED))
    before = (state.orders["B-1"], dict(state.executions))
    for kind in (E.ORDER_ACCEPTED, E.ORDER_REJECTED, E.ORDER_CANCELLED, E.ORDER_EXPIRED):
        classify_venue_event(state, event(kind))
    classify_venue_event(state, fill())
    assert (state.orders["B-1"], dict(state.executions)) == before
