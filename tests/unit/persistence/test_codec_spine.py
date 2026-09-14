"""The persistence codec spine: deterministic JSON out, typed errors back in.

What remains of :mod:`alphalab.persistence` after v2.17 is two things -- the
codec spine tested here, and :class:`~alphalab.persistence.run_store.RunStateStore`,
tested in ``tests/regression/test_run_state_store.py``. The nine-module store
this file used to exercise alongside them (``MemoryStorage``,
``PersistenceEngine``, ``PersistenceAdapter``, ``PersistenceState`` and the
views and validators around them) was deprecated in v2.13 and removed in v2.17;
see ADR-0034.

The spine is the half that was always load-bearing: every snapshot module in the
repository imports :func:`~alphalab.persistence.serializer.serialize` and
:func:`~alphalab.persistence.serializer.deserialize`, and the determinism
asserted here -- sorted keys, no whitespace, ``Decimal`` as a string that keeps
its exact scale -- is what makes two runs of the same inputs produce byte-equal
payloads.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID

import pytest

from alphalab.common.append_log import AppendOnlyLog
from alphalab.persistence import SerializationError, deserialize, serialize


@dataclass(frozen=True)
class DummyDomainEvent:
    trade_id: str
    price: Decimal


class _Colour(Enum):
    RED = 1


def test_a_primitive_payload_round_trips_deterministically() -> None:
    payload = serialize({"key": "value", "list": [1, 2, 3]})
    assert payload == '{"key":"value","list":[1,2,3]}'
    assert deserialize(payload)["key"] == "value"


def test_a_decimal_keeps_its_exact_scale_as_a_string() -> None:
    """``150.50`` must not become ``150.5``: money is exact at its minor unit."""

    payload = serialize({"price": Decimal("150.50")})
    assert payload == '{"price":"150.50"}'
    assert deserialize(payload)["price"] == "150.50"


def test_a_dataclass_serializes_with_sorted_keys() -> None:
    payload = serialize(DummyDomainEvent("T1", Decimal("100.00")))
    assert payload == '{"price":"100.00","trade_id":"T1"}'


def test_an_append_only_log_serializes_as_the_sequence_it_stands_for() -> None:
    assert serialize(AppendOnlyLog([1, 2, 3])) == "[1,2,3]"


def test_an_enum_and_a_uuid_have_explicit_branches() -> None:
    assert serialize({"c": _Colour.RED}) == '{"c":"_Colour.RED"}'
    identifier = UUID("12345678-1234-5678-1234-567812345678")
    assert serialize({"id": identifier}) == '{"id":"12345678-1234-5678-1234-567812345678"}'


def test_an_unsupported_type_is_refused_rather_than_stringified() -> None:
    """A silent ``str()`` fallback is how ``"AppendOnlyLog([...])"`` got persisted."""

    with pytest.raises(SerializationError, match="No deterministic JSON representation"):
        serialize({"impossible": object()})


def test_corrupt_json_is_a_typed_error() -> None:
    with pytest.raises(SerializationError, match="Corrupt JSON"):
        deserialize('{"broken":')
