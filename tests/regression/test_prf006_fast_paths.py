"""The v3.11 performance pass changed how things are computed, never what (ledger PRF-006).

Each fast path here replaced a slower computation of the same value. These
tests hold each one to the computation it replaced -- by exhaustive or random
comparison against the original, never against a figure -- so a later change
that made one of them faster *and different* fails here rather than in a
number somebody reads.
"""

from __future__ import annotations

import copy
import dataclasses
import pickle
import random
import re
import uuid
from dataclasses import InitVar, dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

import pytest

from alphalab.common.currency_units import MAX_MINOR_UNITS, MINOR_UNIT_QUANTA
from alphalab.common.evolve import evolve
from alphalab.common.ids import DeterministicIdSource, _version_4_text, new_id
from alphalab.common.persistent_map import PersistentMap
from alphalab.common.serialization import to_serializable
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.oms.engine import OMSEngine
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order
from alphalab.oms.state import OMSState
from alphalab.portfolio.book import PositionBook
from alphalab.portfolio.money import ZERO_MONEY
from alphalab.portfolio.position import Position

# --------------------------------------------------------------------------- #
# Identifier text
# --------------------------------------------------------------------------- #


def test_version_4_text_is_the_uuid_modules_text_for_every_bit_pattern_tried() -> None:
    rng = random.Random(606)
    edges = [0, (1 << 128) - 1, 1 << 127, (1 << 64) - 1, 0xC000 << 48, 0xF000 << 64]
    for bits in [*edges, *(rng.getrandbits(128) for _ in range(20_000))]:
        assert _version_4_text(bits) == str(uuid.UUID(int=bits, version=4))


def test_a_minted_identifier_is_a_version_4_uuid() -> None:
    shape = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    minted = {new_id() for _ in range(2_000)}

    assert len(minted) == 2_000
    for text in minted:
        assert shape.match(text)
        parsed = uuid.UUID(text)
        assert parsed.version == 4
        assert parsed.variant == uuid.RFC_4122


def test_a_seeded_stream_is_the_one_uuid_would_have_minted() -> None:
    source = DeterministicIdSource(31)
    reference = random.Random(31)

    for _ in range(500):
        assert source() == str(uuid.UUID(int=reference.getrandbits(128), version=4))


# --------------------------------------------------------------------------- #
# evolve == dataclasses.replace
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Plain:
    a: int
    b: str = "b"
    c: list[int] = field(default_factory=list)
    derived: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "derived", self.a * 2)


@dataclass(frozen=True)
class _KeywordOnly:
    a: int
    b: int = field(kw_only=True, default=1)


@dataclass
class _WithInitVar:
    a: int
    seed: InitVar[int] = 3
    tag: ClassVar[str] = "x"

    def __post_init__(self, seed: int) -> None:
        self.a += 0 * seed


class _Subclass(_Plain):
    pass


@pytest.mark.parametrize(
    ("instance", "changes"),
    [
        (_Plain(1), {"a": 5}),
        (_Plain(1, "x", [1]), {}),
        (_Plain(1), {"b": "y", "c": [2]}),
        (_KeywordOnly(1, b=2), {"a": 3}),
        (_WithInitVar(1), {"a": 2}),
        (_Subclass(4), {"a": 6}),
    ],
)
def test_evolve_builds_what_replace_builds(instance: Any, changes: dict[str, Any]) -> None:
    evolved = evolve(instance, **changes)
    replaced = dataclasses.replace(instance, **changes)

    assert evolved == replaced
    assert type(evolved) is type(replaced)
    assert [getattr(evolved, f.name) for f in dataclasses.fields(evolved)] == [
        getattr(replaced, f.name) for f in dataclasses.fields(replaced)
    ]


@pytest.mark.parametrize("changes", [{"derived": 1}, {"missing": 1}])
def test_evolve_refuses_what_replace_refuses_with_the_same_error(changes: dict[str, Any]) -> None:
    with pytest.raises(Exception) as replaced:
        dataclasses.replace(_Plain(1), **changes)
    with pytest.raises(type(replaced.value)) as evolved:
        evolve(_Plain(1), **changes)

    assert str(evolved.value) == str(replaced.value)


def test_evolve_of_something_that_is_not_a_dataclass_fails_as_replace_does() -> None:
    with pytest.raises(TypeError):
        evolve(object())


# --------------------------------------------------------------------------- #
# OrderId: a cached hash, the same hash
# --------------------------------------------------------------------------- #


def test_an_order_id_hashes_as_the_generated_dataclass_hash_did() -> None:
    for _ in range(1_000):
        order_id = OrderId(uuid.uuid4())
        assert hash(order_id) == hash((order_id.value,))


def test_an_order_id_is_still_a_frozen_one_field_value() -> None:
    order_id = OrderId(uuid.UUID(int=7, version=4))

    assert [f.name for f in dataclasses.fields(order_id)] == ["value"]
    assert to_serializable(order_id) == {"value": order_id.value}
    assert pickle.loads(pickle.dumps(order_id)) == order_id
    assert hash(pickle.loads(pickle.dumps(order_id))) == hash(order_id)
    assert copy.deepcopy(order_id) == order_id
    with pytest.raises(dataclasses.FrozenInstanceError):
        order_id.value = uuid.uuid4()  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# PersistentMap: flat insertion bookkeeping, same values and order
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", range(40))
def test_every_version_of_a_branching_history_reads_as_its_dict(seed: int) -> None:
    rng = random.Random(seed)
    history: list[tuple[PersistentMap[int, int], dict[int, int]]] = [(PersistentMap(), {})]
    for _ in range(rng.randint(50, 400)):
        # Mostly extend the newest version; sometimes branch from an older one.
        index = rng.randrange(len(history)) if rng.random() < 0.15 else len(history) - 1
        view, model = history[index]
        key = rng.randrange(10)
        model = dict(model)
        if key in model and rng.random() < 0.45:
            view = view.delete(key)
            del model[key]
        else:
            value = rng.randrange(1_000)
            view = view.set(key, value)
            model[key] = value
        history.append((view, model))

    for view, model in history:
        assert list(view.items()) == list(model.items())
        assert len(view) == len(model)


# --------------------------------------------------------------------------- #
# PositionBook: one delta update, the fresh sum's number and spelling
# --------------------------------------------------------------------------- #


def _position(quantity: str, price: str, currency: str) -> Position:
    return Position(
        asset_id="A",
        quantity=Decimal(quantity),
        average_cost=Decimal("100"),
        market_price=Decimal(price),
        realized_pnl=ZERO_MONEY,
        currency=currency,
        last_updated=1.0,
    )


@pytest.mark.parametrize("currency", ["USD", "JPY", "KWD"])
def test_a_position_re_marked_on_its_side_keeps_fresh_sum_totals(currency: str) -> None:
    rng = random.Random(currency)
    other = Position(
        asset_id="B",
        quantity=Decimal("3"),
        average_cost=Decimal("10"),
        market_price=Decimal("11.5"),
        realized_pnl=ZERO_MONEY,
        currency=currency,
        last_updated=1.0,
    )
    book = PositionBook({"B": other})
    for step in range(300):
        sign = -1 if (step // 50) % 2 else 1
        quantity = str(sign * rng.randint(1, 500))
        price = str(Decimal(rng.randint(1, 10**6)) / Decimal(10 ** rng.randint(0, 4)))
        position = _position(quantity, price, currency)
        book = book.set("A", position)
        fresh = PositionBook({"B": other, "A": position})
        kept, expected = book.totals(currency), fresh.totals(currency)
        assert kept == expected
        assert [str(getattr(kept, name)) for name in ("long_value", "short_value")] == [
            str(getattr(expected, name)) for name in ("long_value", "short_value")
        ]
        assert str(kept.unrealized_pnl) == str(expected.unrealized_pnl)


def test_the_minor_unit_quanta_are_the_quanta() -> None:
    assert len(MINOR_UNIT_QUANTA) == MAX_MINOR_UNITS + 1
    for units, quantum in enumerate(MINOR_UNIT_QUANTA):
        assert quantum == Decimal(1).scaleb(-units)
        assert str(quantum) == str(Decimal(1).scaleb(-units))


# --------------------------------------------------------------------------- #
# The OMS writes an index only when it changes
# --------------------------------------------------------------------------- #


def _order() -> Order:
    return Order(
        order_id=OrderId(uuid.uuid4()),
        strategy_id="S",
        asset_id="AAPL",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        status=OrderStatus.NEW,
        quantity=Decimal("100"),
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("100"),
        limit_price=Decimal("150"),
        stop_price=None,
        average_fill_price=Decimal("0"),
        created_at=1.0,
        updated_at=1.0,
    )


def test_a_transition_that_keeps_an_order_working_leaves_the_index_untouched() -> None:
    order = _order()
    submitted = OMSEngine.submit(OMSState(), order, 1.0)
    accepted = OMSEngine.accept(submitted, order.order_id, 2.0)

    assert accepted.working_by_asset is submitted.working_by_asset
    assert accepted.working_orders_for("AAPL") == (order.order_id,)

    filled = OMSEngine.fill(accepted, order.order_id, Decimal("100"), Decimal("150"), 3.0)
    assert filled.working_orders_for("AAPL") == ()
    assert "AAPL" not in filled.working_by_asset
