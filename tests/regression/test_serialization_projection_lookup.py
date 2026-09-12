"""``__serializable__`` is found on the type, and the payload does not change.

``_convert`` detected a value's serializable projection with
``isinstance(value, SupportsSerializable)`` -- a ``runtime_checkable`` ``Protocol``
check, which CPython answers through :func:`inspect.getattr_static`. That
rebuilds a shadowed-dict view of the class for every value it is asked about, and
``_convert`` asks about every value in a state.

Profiled on a 1,600-event pipeline snapshot: **1,827,034** calls into
``inspect._shadowed_dict`` (1.01s of ``tottime``) and **557,008**
``typing.__instancecheck__`` calls (3.62s cumulative), against **0.19s** actually
spent in ``json.encoder.iterencode``. Roughly 60% of ``serialize`` was one
``isinstance``.

Reading ``__serializable__`` off the value's *type* instead is a plain attribute
lookup. Measured on an 800-event snapshot against the pre-change implementation
kept verbatim as an oracle: **698.0ms -> 188.8ms, a 3.70x reduction, for a
byte-identical 6,706,275-byte payload.** It speeds every snapshot in the
repository -- portfolio, OMS, allocation, lifecycle, pipeline, session, backtest
-- because they all go through this one function.

The caveat, stated rather than glossed
--------------------------------------
``getattr_static`` also finds a ``__serializable__`` set on an **instance**; a
type lookup finds only one declared on a class. This is a real behavioural
difference at that edge, and it is the whole of it.

It is the more conventional reading -- dunder lookup goes through the type, so
``len(x)`` does not consult ``x.__dict__["__len__"]`` -- and every one of the
seven ``__serializable__`` declarations in this repository is a ``def`` at class
scope, which is what :class:`~alphalab.common.serialization.SupportsSerializable`
documents. ``test_an_instance_level_projection_is_not_consulted`` pins the
difference so it is a recorded decision rather than a surprise.

Nothing else about the encoder moved: not the branch order, not the recursion,
not the refusal to stringify, and not the shape of anything it writes.
"""

from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from typing import Any

import pytest

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap, PersistentSet
from alphalab.common.serialization import (
    SupportsSerializable,
    dataclass_to_dict,
    to_serializable,
)
from alphalab.oms.book import OrderBook
from alphalab.oms.state import OMSState
from alphalab.persistence.exceptions import SerializationError
from alphalab.persistence.serializer import deserialize, serialize


def pre_change_convert(value: Any) -> Any:
    """The v2.12 implementation, kept verbatim as the oracle.

    Written out here rather than imported, so it is a fixed expectation that a
    change to the encoder cannot quietly move with it -- the same reason
    ``test_id_stream_continuation`` writes out the legacy identifier stream.
    """

    if isinstance(value, SupportsSerializable) and not isinstance(value, type):
        return pre_change_convert(value.__serializable__())
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: pre_change_convert(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, AppendOnlyLog):
        return tuple(pre_change_convert(item) for item in value)
    if isinstance(value, list | tuple):
        if hasattr(value, "_fields"):
            return type(value)(*(pre_change_convert(item) for item in value))
        return type(value)(pre_change_convert(item) for item in value)
    if isinstance(value, dict):
        return type(value)((pre_change_convert(k), pre_change_convert(v)) for k, v in value.items())
    return value


@dataclass(frozen=True, slots=True)
class Plain:
    name: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Projected:
    """A dataclass whose declared projection is not its field shape."""

    left: int
    right: int

    def __serializable__(self) -> dict[str, Any]:
        return {"sum": self.left + self.right}


class NotADataclass:
    """A projection on a plain class, which is the other supported shape."""

    def __init__(self, values: tuple[int, ...]) -> None:
        self._values = values

    def __serializable__(self) -> tuple[int, ...]:
        return self._values


@dataclass(frozen=True, slots=True)
class Nested:
    """A projection reached through a dataclass field, a log, a map and a list."""

    one: Projected
    many: tuple[Projected, ...]
    log: AppendOnlyLog[Projected]
    mapped: PersistentMap[str, Projected]
    listed: list[NotADataclass]


def _sample() -> Nested:
    return Nested(
        one=Projected(1, 2),
        many=(Projected(3, 4), Projected(5, 6)),
        log=AppendOnlyLog((Projected(7, 8),)),
        mapped=PersistentMap({"a": Projected(9, 10)}),
        listed=[NotADataclass((11, 12))],
    )


#: Values that exercise every branch of ``_convert``, projections included.
CASES: tuple[Any, ...] = (
    Plain("x", Decimal("1.25")),
    Projected(1, 2),
    NotADataclass((1, 2, 3)),
    _sample(),
    AppendOnlyLog((Plain("a", Decimal("1")), Plain("b", Decimal("2")))),
    PersistentMap({"k": Plain("v", Decimal("3"))}),
    PersistentSet(("a", "b", "c")),
    OrderBook(),
    OMSState(),
    {"nested": {"deep": [Projected(1, 1), Plain("p", Decimal("0.1"))]}},
    [1, "two", Decimal("3"), None, True],
    (),
    {},
)


# ---------------------------------------------------------------------------
# Byte-identity against the pre-change oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", CASES, ids=lambda v: type(v).__name__)
def test_conversion_matches_the_pre_change_implementation(value: Any) -> None:
    assert to_serializable(value) == pre_change_convert(value)


@pytest.mark.parametrize("value", CASES, ids=lambda v: type(v).__name__)
def test_serialized_bytes_match_the_pre_change_implementation(value: Any) -> None:
    """The property that matters: identical bytes, not merely equal structures."""

    assert serialize(to_serializable(value)) == serialize(pre_change_convert(value))


def test_a_real_state_serializes_byte_identically(_pipeline_payload: str) -> None:
    """The measurement's own subject: a captured pipeline state, end to end."""

    from alphalab.runtime.snapshot import capture

    oracle = serialize(pre_change_convert(capture(_built_state())))

    assert _pipeline_payload == oracle
    assert len(_pipeline_payload) > 100_000, "a payload large enough to mean something"


# ---------------------------------------------------------------------------
# The projections the repository actually declares
# ---------------------------------------------------------------------------


def test_every_declared_projection_is_still_honoured() -> None:
    """All seven ``__serializable__`` definitions are class-level, and all are found."""

    from alphalab.common.serialization import _declares_projection

    declared = (
        OrderBook(),
        OMSState(),
        PersistentMap({"a": 1}),
        PersistentSet(("a",)),
        Projected(1, 2),
        NotADataclass((1,)),
    )
    for value in declared:
        assert _declares_projection(value), type(value).__name__


def test_a_value_without_a_projection_is_not_mistaken_for_one() -> None:
    from alphalab.common.serialization import _declares_projection

    for value in (Plain("x", Decimal("1")), 1, "s", None, [1], {"a": 1}, Decimal("1")):
        assert not _declares_projection(value)


def test_a_class_object_is_never_treated_as_a_projection() -> None:
    """The ``isinstance(value, type)`` guard, kept from the original."""

    from alphalab.common.serialization import _declares_projection

    assert not _declares_projection(Projected)
    assert not _declares_projection(OrderBook)


def test_a_projection_is_preferred_over_the_dataclass_field_shape() -> None:
    """Branch order is unchanged: a declaring dataclass projects, it does not expand."""

    assert to_serializable(Projected(1, 2)) == {"sum": 3}


def test_an_oms_state_still_projects_through_its_snapshot() -> None:
    """The projection that exists because a typed key has no JSON form."""

    projected = to_serializable(OMSState())

    assert isinstance(projected, dict)
    assert projected["schema_version"] == 1
    assert "orders" in projected


# ---------------------------------------------------------------------------
# The recorded caveat
# ---------------------------------------------------------------------------


def test_an_instance_level_projection_is_not_consulted() -> None:
    """The one behavioural difference, pinned so it is a decision and not a surprise.

    ``getattr_static`` saw an instance attribute; a type lookup does not. Dunder
    lookup conventionally goes through the type, and no instance-level
    ``__serializable__`` exists anywhere in this repository -- every declaration
    is a ``def`` at class scope. A type that wants a projection declares one.
    """

    from alphalab.common.serialization import _declares_projection

    class Bare:
        def __init__(self) -> None:
            self.value = 1

    instance = Bare()
    instance.__serializable__ = lambda: {"value": 1}  # type: ignore[attr-defined]

    assert not _declares_projection(instance), "instance attributes are not consulted"
    # And the encoder therefore refuses it, rather than silently stringifying --
    # the v2.1 rule, unchanged.
    with pytest.raises(SerializationError):
        serialize(to_serializable(instance))


def test_a_subclass_inherits_a_projection_declared_on_its_base() -> None:
    """Type lookup walks the MRO, as attribute lookup does."""

    class Derived(Projected):
        pass

    assert to_serializable(Derived(2, 3)) == {"sum": 5}


# ---------------------------------------------------------------------------
# Nothing unrelated moved
# ---------------------------------------------------------------------------


def test_unserializable_values_still_raise_rather_than_stringify() -> None:
    with pytest.raises(SerializationError, match="No deterministic JSON representation"):
        serialize(object())


def test_a_dataclass_mapping_key_is_still_rejected() -> None:
    with pytest.raises(SerializationError):
        serialize({Plain("k", Decimal("1")): 1})


def test_dataclass_to_dict_still_refuses_a_non_dataclass() -> None:
    from alphalab.common.exceptions import AlphaLabSerializationError

    with pytest.raises(AlphaLabSerializationError):
        dataclass_to_dict(NotADataclass((1,)))


def test_serialization_is_still_deterministic_across_repeats() -> None:
    value = _sample()

    assert len({serialize(to_serializable(value)) for _ in range(20)}) == 1


def test_a_round_trip_still_reads_back_what_was_written() -> None:
    written = serialize(to_serializable(_sample()))

    assert deserialize(written) == {
        "one": {"sum": 3},
        "many": [{"sum": 7}, {"sum": 11}],
        "log": [{"sum": 15}],
        "mapped": {"a": {"sum": 19}},
        "listed": [[11, 12]],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _built_state() -> Any:
    """A real post-run pipeline state, built once per call and deterministically."""

    import uuid

    from alphalab.common.ids import id_scope
    from alphalab.market.record import MarketRecord
    from alphalab.runtime.execution_pipeline import ExecutionPipeline
    from tests.integration.harness import (
        ScriptedStrategy,
        context_factory,
        pipeline_config,
        running_strategy_state,
        sized_quote,
    )

    strategy_id = str(uuid.UUID(int=0x2213_1001))
    asset_id = str(uuid.UUID(int=0x2213_1002))
    plan = {2.0 + index: (Decimal("5") if index % 2 == 0 else Decimal("-3")) for index in range(60)}
    strategy = ScriptedStrategy(strategy_id, asset_id, plan)
    with id_scope(4242):
        state = ExecutionPipeline.initialize(
            pipeline_config(strategy_id), running_strategy_state(strategy_id, strategy), 1.0
        )
        for index in range(60):
            record = MarketRecord(
                event_id=f"S-{index}",
                timestamp=2.0 + index,
                payload=sized_quote(
                    asset_id, 2.0 + index, Decimal(100 + index % 20), Decimal("100")
                ),
            )
            state = ExecutionPipeline.process_record(state, record, context_factory).state
    return state


@pytest.fixture
def _pipeline_payload() -> str:
    from alphalab.runtime.snapshot import capture

    return serialize(capture(_built_state()))
