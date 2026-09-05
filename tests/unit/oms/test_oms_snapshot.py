"""Unit tests for OMS state snapshots (capture / restore / decode)."""

from decimal import Decimal

import pytest

from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.oms.engine import OMSEngine
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order
from alphalab.oms.snapshot import (
    SnapshotDecodeError,
    capture,
    from_primitives,
    restore,
)
from alphalab.oms.state import OMSState
from alphalab.persistence.serializer import deserialize, serialize


def _order(order_id: OrderId, asset: str = "AAPL", strategy: str = "STRAT") -> Order:
    return Order(
        order_id=order_id,
        strategy_id=strategy,
        asset_id=asset,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        status=OrderStatus.NEW,
        quantity=Decimal("10"),
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("10"),
        limit_price=Decimal("100.0050"),
        stop_price=None,
        average_fill_price=Decimal("0"),
        created_at=1.0,
        updated_at=1.0,
        metadata={"reference_price": "100.005"},
    )


def _populated() -> tuple[OMSState, OrderId, OrderId]:
    """A state with one part-filled working order and one cancelled order."""

    state = OMSState()
    working = OrderId.generate()
    cancelled = OrderId.generate()

    state = OMSEngine.submit(state, _order(working), 1.0)
    state = OMSEngine.accept(state, working, 2.0)
    state = OMSEngine.partial_fill(state, working, Decimal("4"), Decimal("100.0050"), 3.0)

    state = OMSEngine.submit(state, _order(cancelled, asset="MSFT", strategy="OTHER"), 1.0)
    state = OMSEngine.accept(state, cancelled, 2.0)
    state = OMSEngine.cancel(state, cancelled, 4.0)
    return state, working, cancelled


def test_capture_records_orders_in_submission_order() -> None:
    state, working, cancelled = _populated()

    snapshot = capture(state)

    assert [order.order_id for order in snapshot.orders] == [working, cancelled]


def test_capture_records_the_active_completed_partition() -> None:
    state, working, cancelled = _populated()

    snapshot = capture(state)

    assert snapshot.active_orders == (working,)
    assert snapshot.completed_orders == (cancelled,)


def test_capture_tags_every_event_with_its_type() -> None:
    state, _, _ = _populated()

    snapshot = capture(state)

    assert [record.event_type for record in snapshot.events] == [
        "OrderSubmitted",
        "OrderAccepted",
        "OrderPartiallyFilled",
        "OrderSubmitted",
        "OrderAccepted",
        "OrderCancelled",
    ]


def test_restore_is_the_inverse_of_capture() -> None:
    state, _, _ = _populated()

    assert restore(capture(state)) == state


def test_restore_rebuilds_the_asset_and_strategy_indices() -> None:
    """The indices are omitted from the payload, so they must be derivable."""

    state, working, cancelled = _populated()

    restored = restore(capture(state))

    assert [o.order_id for o in restored.orders.orders_for_asset("AAPL")] == [working]
    assert [o.order_id for o in restored.orders.orders_for_asset("MSFT")] == [cancelled]
    assert [o.order_id for o in restored.orders.orders_for_strategy("OTHER")] == [cancelled]


def test_snapshot_survives_a_json_round_trip() -> None:
    state, _, _ = _populated()

    restored = restore(from_primitives(deserialize(serialize(state))))

    assert restored == state


def test_json_round_trip_preserves_order_identity_and_numbers() -> None:
    state, working, _ = _populated()

    restored = restore(from_primitives(deserialize(serialize(state))))
    order = restored.orders.find(working)

    assert order.order_id == working
    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("4")
    assert order.remaining_quantity == Decimal("6")
    assert order.limit_price == Decimal("100.0050")
    assert order.metadata == {"reference_price": "100.005"}


def test_an_empty_state_round_trips() -> None:
    assert restore(from_primitives(deserialize(serialize(OMSState())))) == OMSState()


def test_an_unknown_event_type_is_rejected_not_guessed() -> None:
    payload = deserialize(serialize(_populated()[0]))
    payload["events"][0]["event_type"] = "OrderTeleported"

    with pytest.raises(SnapshotDecodeError, match="Unknown OMS event type"):
        from_primitives(payload)


def test_a_missing_field_is_rejected() -> None:
    payload = deserialize(serialize(_populated()[0]))
    del payload["active_orders"]

    with pytest.raises(SnapshotDecodeError, match="missing 'active_orders'"):
        from_primitives(payload)


def test_a_malformed_order_id_is_rejected() -> None:
    payload = deserialize(serialize(_populated()[0]))
    payload["orders"][0]["order_id"]["value"] = "not-a-uuid"

    with pytest.raises(SnapshotDecodeError, match="Not a valid OrderId"):
        from_primitives(payload)


def test_a_non_array_order_field_is_rejected() -> None:
    payload = deserialize(serialize(_populated()[0]))
    payload["orders"] = {"nope": 1}

    with pytest.raises(SnapshotDecodeError, match="not an array"):
        from_primitives(payload)
