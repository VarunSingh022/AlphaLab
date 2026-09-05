"""Regression guard for whole-state OMS serialization, added in v2.2.

On v2.0.0 and v2.1, ``serialize(OMSState())`` raised: the order book indexes
orders by ``OrderId``, a dataclass, and neither ``dataclasses.asdict`` nor
``json.dumps`` accepts a dataclass as a mapping key. The state's *logs*
serialized correctly -- the limitation was the typed identifier -- but replay
and persistence need complete snapshots, not partial ones.

v2.2 fixes it without weakening the identifier: ``OrderId`` stays a dataclass in
memory, and the state declares an explicit projection in which orders are an
array. These tests pin every property that projection has to have, and pin that
the encoder is still strict about everything else.
"""

import json
from decimal import Decimal

import pytest

from alphalab.common.serialization import dataclass_to_dict
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.oms.engine import OMSEngine
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order
from alphalab.oms.snapshot import capture, from_primitives, restore
from alphalab.oms.state import OMSState
from alphalab.persistence.adapter import PersistenceAdapter
from alphalab.persistence.exceptions import SerializationError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.persistence.state import PersistenceState
from alphalab.persistence.validation import validate_snapshot_save


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


def _state() -> tuple[OMSState, OrderId, OrderId]:
    state = OMSState()
    working = OrderId.generate()
    done = OrderId.generate()

    state = OMSEngine.submit(state, _order(working), 1.0)
    state = OMSEngine.accept(state, working, 2.0)
    state = OMSEngine.partial_fill(state, working, Decimal("4"), Decimal("100.0050"), 3.0)

    state = OMSEngine.submit(state, _order(done, asset="MSFT", strategy="OTHER"), 1.0)
    state = OMSEngine.accept(state, done, 2.0)
    state = OMSEngine.fill(state, done, Decimal("10"), Decimal("99.9950"), 4.0)
    return state, working, done


# ---------------------------------------------------------------------------
# 1. The whole state serializes
# ---------------------------------------------------------------------------


def test_the_whole_oms_state_serializes() -> None:
    state, _, _ = _state()

    decoded = json.loads(serialize(state))

    assert set(decoded) == {
        "orders",
        "active_orders",
        "completed_orders",
        "history",
        "events",
    }


def test_no_dataclass_is_used_as_a_json_mapping_key() -> None:
    """The exact defect: orders are an array, keyed by nothing."""

    state, _, _ = _state()

    decoded = json.loads(serialize(state))

    assert isinstance(decoded["orders"], list)
    assert isinstance(decoded["active_orders"], list)
    assert isinstance(decoded["completed_orders"], list)


def test_order_book_contents_survive_serialization() -> None:
    state, working, done = _state()

    decoded = json.loads(serialize(state))
    orders = decoded["orders"]

    assert [o["order_id"]["value"] for o in orders] == [
        str(working.value),
        str(done.value),
    ]
    assert orders[0]["status"] == "partially_filled"
    assert orders[0]["filled_quantity"] == "4"
    assert orders[1]["status"] == "filled"
    assert orders[1]["asset_id"] == "MSFT"


def test_the_active_completed_partition_survives_serialization() -> None:
    state, working, done = _state()

    decoded = json.loads(serialize(state))

    assert [o["value"] for o in decoded["active_orders"]] == [str(working.value)]
    assert [o["value"] for o in decoded["completed_orders"]] == [str(done.value)]


def test_every_event_carries_its_type() -> None:
    """Without a tag a heterogenous log cannot be read back into typed events."""

    state, _, _ = _state()

    decoded = json.loads(serialize(state))

    assert [record["event_type"] for record in decoded["events"]] == [
        "OrderSubmitted",
        "OrderAccepted",
        "OrderPartiallyFilled",
        "OrderSubmitted",
        "OrderAccepted",
        "OrderFilled",
    ]
    assert decoded["events"][2]["event"]["fill_quantity"] == "4"


# ---------------------------------------------------------------------------
# 2. Deterministic
# ---------------------------------------------------------------------------


def test_serializing_the_same_state_twice_is_byte_identical() -> None:
    state, _, _ = _state()

    assert serialize(state) == serialize(state)


def test_order_and_set_ordering_is_stable_not_hash_dependent() -> None:
    """``frozenset`` iteration ordered these before; insertion order does now."""

    state, _, _ = _state()

    first = json.loads(serialize(state))
    second = json.loads(serialize(restore(capture(state))))

    assert first["orders"] == second["orders"]
    assert first["active_orders"] == second["active_orders"]
    assert first["completed_orders"] == second["completed_orders"]


# ---------------------------------------------------------------------------
# 3. Round trip
# ---------------------------------------------------------------------------


def test_a_snapshot_round_trips_back_into_an_equal_state() -> None:
    state, _, _ = _state()

    assert restore(from_primitives(deserialize(serialize(state)))) == state


def test_round_trip_preserves_order_identity() -> None:
    state, working, done = _state()

    restored = restore(from_primitives(deserialize(serialize(state))))

    assert restored.orders.find(working).order_id == working
    assert restored.orders.find(done).order_id == done
    assert working in restored.active_orders
    assert done in restored.completed_orders


def test_round_trip_preserves_the_derived_indices() -> None:
    state, working, done = _state()

    restored = restore(from_primitives(deserialize(serialize(state))))

    assert [o.order_id for o in restored.orders.orders_for_asset("AAPL")] == [working]
    assert [o.order_id for o in restored.orders.orders_for_strategy("OTHER")] == [done]


def test_round_trip_preserves_the_event_log_as_typed_events() -> None:
    state, _, _ = _state()

    restored = restore(from_primitives(deserialize(serialize(state))))

    assert list(restored.events) == list(state.events)
    assert list(restored.history) == list(state.history)
    assert [type(e).__name__ for e in restored.events] == [type(e).__name__ for e in state.events]


def test_a_restored_state_keeps_working() -> None:
    """A snapshot is only complete if the engine can carry on from it."""

    state, working, _ = _state()
    restored = restore(from_primitives(deserialize(serialize(state))))

    filled = OMSEngine.fill(restored, working, Decimal("6"), Decimal("100.0050"), 5.0)

    assert filled.orders.find(working).status is OrderStatus.FILLED
    assert working in filled.completed_orders
    assert working not in filled.active_orders


def test_a_persisted_snapshot_validates_and_round_trips() -> None:
    state, _, _ = _state()

    snapshot = PersistenceAdapter.to_snapshot("snap-oms", "oms", 9.0, state)
    validate_snapshot_save(PersistenceState(engine_id="engine-oms"), snapshot)

    assert restore(from_primitives(deserialize(snapshot.payload))) == state


# ---------------------------------------------------------------------------
# 4. Strictness is not weakened
# ---------------------------------------------------------------------------


def test_unserializable_values_still_raise_rather_than_stringify() -> None:
    class Opaque:
        def __repr__(self) -> str:
            return "Opaque()"

    with pytest.raises(SerializationError, match="No deterministic JSON representation"):
        serialize({"bad": Opaque()})


def test_a_raw_dataclass_keyed_mapping_is_still_rejected() -> None:
    """The projection is explicit, not a blanket coercion of unknown keys."""

    with pytest.raises(SerializationError):
        serialize({"book": {OrderId.generate(): "value"}})


def test_dataclass_to_dict_applies_the_projection() -> None:
    state, _, _ = _state()

    converted = dataclass_to_dict(state)

    assert isinstance(converted["orders"], tuple)
    assert all(isinstance(order, dict) for order in converted["orders"])
    assert converted["events"][0]["event_type"] == "OrderSubmitted"
