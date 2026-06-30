"""Unit tests for the immutable Order Management System."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from alphalab.oms import (
    OMSEngine,
    OMSState,
    Order,
    OrderId,
    OrderStatus,
    OrderType,
    Side,
)
from alphalab.oms.exceptions import (
    DuplicateOrderError,
    InvalidTransitionError,
    UnknownOrderError,
)


@pytest.fixture
def order_id() -> OrderId:
    return OrderId(uuid.uuid4())


@pytest.fixture
def order(order_id: OrderId) -> Order:
    return Order(
        order_id=order_id,
        strategy_id="strategy-001",
        asset_id="AAPL",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        status=OrderStatus.NEW,
        quantity=Decimal("100"),
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("100"),
        limit_price=Decimal("150.00"),
        stop_price=None,
        average_fill_price=Decimal("0"),
        created_at=1.0,
        updated_at=1.0,
    )


@pytest.fixture
def state() -> OMSState:
    return OMSState()


def test_submit_order(
    state: OMSState,
    order: Order,
) -> None:

    new_state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    assert state is not new_state

    assert new_state.orders.contains(order.order_id)

    assert order.order_id in new_state.active_orders

    assert len(new_state.events) == 1

    assert len(new_state.history) == 1


def test_duplicate_order_rejected(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    with pytest.raises(DuplicateOrderError):
        OMSEngine.submit(
            state,
            order,
            current_timestamp=2.0,
        )


def test_accept_order(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    state = OMSEngine.accept(
        state,
        order.order_id,
        timestamp=2.0,
    )

    updated = state.orders.find(
        order.order_id,
    )

    assert updated.status is OrderStatus.ACCEPTED


def test_reject_order(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    state = OMSEngine.reject(
        state,
        order.order_id,
        reason="Risk rejection",
        timestamp=2.0,
    )

    updated = state.orders.find(
        order.order_id,
    )

    assert updated.status is OrderStatus.REJECTED

    assert order.order_id in state.completed_orders

    assert order.order_id not in state.active_orders


def test_cancel_order(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    state = OMSEngine.accept(
        state,
        order.order_id,
        timestamp=2.0,
    )

    state = OMSEngine.cancel(
        state,
        order.order_id,
        timestamp=3.0,
    )

    updated = state.orders.find(
        order.order_id,
    )

    assert updated.status is OrderStatus.CANCELLED

    assert order.order_id in state.completed_orders

    assert order.order_id not in state.active_orders


def test_expire_order(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    state = OMSEngine.accept(
        state,
        order.order_id,
        timestamp=2.0,
    )

    state = OMSEngine.expire(
        state,
        order.order_id,
        timestamp=3.0,
    )

    updated = state.orders.find(
        order.order_id,
    )

    assert updated.status is OrderStatus.EXPIRED


def test_submit_returns_new_state(
    state: OMSState,
    order: Order,
) -> None:

    new_state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    assert state is not new_state

    assert len(state.history) == 0

    assert len(new_state.history) == 1


def test_accept_returns_new_state(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    accepted = OMSEngine.accept(
        state,
        order.order_id,
        timestamp=2.0,
    )

    assert accepted is not state

    assert (
        state.orders.find(
            order.order_id,
        ).status
        is OrderStatus.NEW
    )

    assert (
        accepted.orders.find(
            order.order_id,
        ).status
        is OrderStatus.ACCEPTED
    )


def test_events_are_recorded(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    state = OMSEngine.accept(
        state,
        order.order_id,
        timestamp=2.0,
    )

    assert len(state.history) == 2

    assert len(state.events) == 2


def test_partial_fill(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    state = OMSEngine.accept(
        state,
        order.order_id,
        timestamp=2.0,
    )

    state = OMSEngine.partial_fill(
        state,
        order.order_id,
        quantity=Decimal("40"),
        price=Decimal("151.50"),
        timestamp=3.0,
    )

    updated = state.orders.find(order.order_id)

    assert updated.status is OrderStatus.PARTIALLY_FILLED
    assert updated.filled_quantity == Decimal("40")
    assert updated.remaining_quantity == Decimal("60")
    assert updated.average_fill_price == Decimal("151.50")


def test_complete_fill(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    state = OMSEngine.accept(
        state,
        order.order_id,
        timestamp=2.0,
    )

    state = OMSEngine.fill(
        state,
        order.order_id,
        quantity=Decimal("100"),
        price=Decimal("152.00"),
        timestamp=3.0,
    )

    updated = state.orders.find(order.order_id)

    assert updated.status is OrderStatus.FILLED
    assert updated.remaining_quantity == Decimal("0")
    assert order.order_id in state.completed_orders
    assert order.order_id not in state.active_orders


def test_replace_quantity(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    state = OMSEngine.accept(
        state,
        order.order_id,
        timestamp=2.0,
    )

    state = OMSEngine.replace(
        state,
        order.order_id,
        quantity=Decimal("250"),
        limit_price=Decimal("149.00"),
        timestamp=3.0,
    )

    updated = state.orders.find(order.order_id)

    assert updated.quantity == Decimal("250")
    assert updated.remaining_quantity == Decimal("250")
    assert updated.limit_price == Decimal("149.00")


def test_unknown_order_lookup(
    state: OMSState,
) -> None:

    unknown = OrderId.generate()

    with pytest.raises(UnknownOrderError):
        OMSEngine.accept(
            state,
            unknown,
            timestamp=1.0,
        )


def test_invalid_transition(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    state = OMSEngine.accept(
        state,
        order.order_id,
        timestamp=2.0,
    )

    state = OMSEngine.fill(
        state,
        order.order_id,
        quantity=Decimal("100"),
        price=Decimal("150.00"),
        timestamp=3.0,
    )

    with pytest.raises(InvalidTransitionError):
        OMSEngine.cancel(
            state,
            order.order_id,
            timestamp=4.0,
        )


def test_history_and_events_match(
    state: OMSState,
    order: Order,
) -> None:

    state = OMSEngine.submit(
        state,
        order,
        current_timestamp=1.0,
    )

    state = OMSEngine.accept(
        state,
        order.order_id,
        timestamp=2.0,
    )

    state = OMSEngine.cancel(
        state,
        order.order_id,
        timestamp=3.0,
    )

    assert len(state.history) == 3
    assert len(state.events) == 3
