"""Complete, restorable snapshots of an :class:`~alphalab.oms.state.OMSState`.

Why the state needs an explicit projection
------------------------------------------
``OMSState`` holds its orders in an :class:`~alphalab.oms.book.OrderBook`, which
indexes them by ``OrderId`` -- a dataclass. JSON object keys are strings, so
neither ``dataclasses.asdict`` nor ``json.dumps`` can encode that mapping, and
until v2.2 the whole state was simply not serializable. Replay and persistence
need whole-state snapshots, so v2.2 gives the state a projection instead of
weakening its typed identifiers:

* orders serialize as an **array**, in submission order, each carrying its own
  ``OrderId`` as a value (which encodes fine) rather than as a key;
* the book's asset and strategy indices are omitted -- they are derived from the
  order array and rebuilt exactly by :func:`restore`;
* the active/completed order sets serialize as arrays of ``OrderId`` in
  insertion order;
* every event carries an explicit ``event_type`` tag, without which a heterogenous
  event log cannot be read back into typed events.

Everything in a snapshot is either a dataclass, a ``Decimal``, an enum, a
``UUID`` or a primitive, so ``alphalab.persistence.serialize`` encodes it with no
new encoder branches -- and still raises on anything it does not recognise.

Round trip
----------
:func:`capture` and :func:`restore` are inverses in memory. Across JSON, decode
the payload with ``alphalab.persistence.deserialize`` and pass it through
:func:`from_primitives` first::

    payload = serialize(state)  # str
    restored = restore(from_primitives(deserialize(payload)))
    assert restored == state

Schema version
--------------
Every payload :func:`capture` produces carries ``schema_version``, and
:func:`from_primitives` refuses a version it does not read -- the rule ADR-0014
set for every snapshot envelope, which this one was the last to adopt.

There is one bounded exception, and it is a decision rather than a default.
Payloads written between v2.2 and v2.8 through the public recipe above carry no
version at all, and unlike the portfolio's refused version 1 they are missing no
data: every field this decoder reads is present. Such a payload is therefore
still readable, but *only* when its top-level keys are exactly
:data:`LEGACY_UNVERSIONED_V0_KEYS`. That is a total structural match, not a
subset test and not "a missing version means 1": a payload one key short, one
key long, or otherwise unversioned is refused, because nothing but a pre-v2.9
writer produces that exact shape. See ADR-0023.

Reading a legacy payload does not make it version 1. It classifies it as
``LEGACY_UNVERSIONED_V0`` and decodes it into the current :class:`OMSSnapshot`,
which -- like every in-memory snapshot -- carries the current constant. Capturing
and serializing that state again writes ``schema_version`` explicitly, which is
the whole of the migration.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentSet
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.oms.book import OrderBook
from alphalab.oms.events import (
    OMSEvent,
    OrderAccepted,
    OrderCancelled,
    OrderExpired,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
    OrderReplaced,
    OrderSubmitted,
)
from alphalab.oms.exceptions import OMSError
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order
from alphalab.oms.state import OMSState
from alphalab.persistence.decode import require_schema_version
from alphalab.persistence.exceptions import StateDecodeError

__all__ = [
    "LEGACY_UNVERSIONED_V0_KEYS",
    "OMS_SNAPSHOT_SCHEMA",
    "OMSEventRecord",
    "OMSSnapshot",
    "SnapshotDecodeError",
    "capture",
    "from_primitives",
    "restore",
]


class SnapshotDecodeError(OMSError):
    """Raised when a snapshot payload cannot be read back into OMS types."""


#: Schema version this module reads and writes. See ADR-0023.
#:
#: Version 1 is the first *declared* OMS snapshot shape, not the first shape:
#: the projection has been stable since v2.2 and no field is added here. It is a
#: module-local literal rather than ``DEFAULT_SCHEMA_VERSION`` for the reason
#: v2.6 gave for the portfolio and v2.8 for the lifecycle -- that constant also
#: versions ``CommonEvent`` and ``BaseEvent``, so bumping it would version every
#: event in the system as a side effect of one subsystem's change.
OMS_SNAPSHOT_SCHEMA: Final = 1

_SUBSYSTEM: Final = "oms"

#: The exact top-level key set of a pre-v2.9, unversioned OMS payload.
#:
#: Recognition is a *total* match against this set, never a subset test. Nothing
#: but a writer older than v2.9 produces exactly these five keys and no
#: ``schema_version``, because every later writer adds one -- which is what makes
#: the match evidence of origin rather than a guess.
LEGACY_UNVERSIONED_V0_KEYS: Final[frozenset[str]] = frozenset(
    {"orders", "active_orders", "completed_orders", "history", "events"}
)


#: Every OMS event type, by the tag written into a snapshot.
_EVENT_TYPES: Mapping[str, type[OMSEvent]] = {
    cls.__name__: cls
    for cls in (
        OrderSubmitted,
        OrderAccepted,
        OrderRejected,
        OrderCancelled,
        OrderExpired,
        OrderReplaced,
        OrderPartiallyFilled,
        OrderFilled,
    )
}


@dataclass(frozen=True, slots=True)
class OMSEventRecord:
    """One OMS event plus the tag needed to read it back as its own type."""

    event_type: str
    event: OMSEvent


@dataclass(frozen=True, slots=True)
class OMSSnapshot:
    """Complete, JSON-serializable projection of an :class:`OMSState`."""

    orders: tuple[Order, ...]
    active_orders: tuple[OrderId, ...]
    completed_orders: tuple[OrderId, ...]
    history: tuple[OMSEventRecord, ...]
    events: tuple[OMSEventRecord, ...]
    schema_version: int = OMS_SNAPSHOT_SCHEMA


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def _records(log: Sequence[OMSEvent]) -> tuple[OMSEventRecord, ...]:
    return tuple(OMSEventRecord(type(event).__name__, event) for event in log)


def capture(state: OMSState) -> OMSSnapshot:
    """Project ``state`` into its complete serializable snapshot."""

    return OMSSnapshot(
        orders=tuple(state.orders.orders()),
        active_orders=tuple(state.active_orders),
        completed_orders=tuple(state.completed_orders),
        history=_records(state.history),
        events=_records(state.events),
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(snapshot: OMSSnapshot) -> OMSState:
    """Rebuild the state a snapshot was captured from.

    The book's asset and strategy indices are rebuilt from the order array, so
    the restored book indexes exactly as the captured one did.
    """

    book = OrderBook()
    for order in snapshot.orders:
        book = book.add(order)

    return OMSState(
        orders=book,
        active_orders=PersistentSet(snapshot.active_orders),
        completed_orders=PersistentSet(snapshot.completed_orders),
        history=AppendOnlyLog(record.event for record in snapshot.history),
        events=AppendOnlyLog(record.event for record in snapshot.events),
    )


# ---------------------------------------------------------------------------
# Decoding a JSON payload back into snapshot types
# ---------------------------------------------------------------------------


def _require(payload: Mapping[str, Any], key: str) -> Any:
    if key not in payload:
        raise SnapshotDecodeError(f"Snapshot payload is missing {key!r}")
    return payload[key]


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive
        raise SnapshotDecodeError(f"{field_name} is not a decimal: {value!r}") from exc


def _optional_decimal(value: Any, field_name: str) -> Decimal | None:
    return None if value is None else _decimal(value, field_name)


def _order_id(value: Any) -> OrderId:
    """Decode an ``OrderId``, which serializes as ``{"value": "<uuid>"}``."""

    raw = value.get("value") if isinstance(value, Mapping) else value
    try:
        return OrderId(UUID(str(raw)))
    except Exception as exc:
        raise SnapshotDecodeError(f"Not a valid OrderId: {value!r}") from exc


def _order(payload: Mapping[str, Any]) -> Order:
    return Order(
        order_id=_order_id(_require(payload, "order_id")),
        strategy_id=str(_require(payload, "strategy_id")),
        asset_id=str(_require(payload, "asset_id")),
        side=Side(_require(payload, "side")),
        order_type=OrderType(_require(payload, "order_type")),
        status=OrderStatus(_require(payload, "status")),
        quantity=_decimal(_require(payload, "quantity"), "quantity"),
        filled_quantity=_decimal(_require(payload, "filled_quantity"), "filled_quantity"),
        remaining_quantity=_decimal(_require(payload, "remaining_quantity"), "remaining_quantity"),
        limit_price=_optional_decimal(payload.get("limit_price"), "limit_price"),
        stop_price=_optional_decimal(payload.get("stop_price"), "stop_price"),
        average_fill_price=_decimal(_require(payload, "average_fill_price"), "average_fill_price"),
        created_at=float(_require(payload, "created_at")),
        updated_at=float(_require(payload, "updated_at")),
        metadata=dict(payload.get("metadata") or {}),
    )


#: How to decode each event field that is not already a JSON primitive.
_EVENT_FIELD_DECODERS: Mapping[str, Callable[[Any], Any]] = {
    "order_id": _order_id,
    "order": lambda value: _order(value),
    "fill_quantity": lambda value: _decimal(value, "fill_quantity"),
    "fill_price": lambda value: _decimal(value, "fill_price"),
    "new_quantity": lambda value: _decimal(value, "new_quantity"),
    "new_limit_price": lambda value: _optional_decimal(value, "new_limit_price"),
    "timestamp": float,
}


def _event(record: Mapping[str, Any]) -> OMSEvent:
    event_type = str(_require(record, "event_type"))
    cls = _EVENT_TYPES.get(event_type)
    if cls is None:
        raise SnapshotDecodeError(f"Unknown OMS event type: {event_type!r}")

    payload = _require(record, "event")
    if not isinstance(payload, Mapping):
        raise SnapshotDecodeError(f"{event_type} payload is not an object: {payload!r}")

    kwargs: dict[str, Any] = {}
    for field in fields(cls):
        raw = _require(payload, field.name)
        decoder = _EVENT_FIELD_DECODERS.get(field.name)
        kwargs[field.name] = decoder(raw) if decoder is not None else raw
    return cls(**kwargs)


def _sequence(payload: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = _require(payload, key)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise SnapshotDecodeError(f"Snapshot field {key!r} is not an array: {value!r}")
    return value


def _require_readable_shape(payload: Mapping[str, Any]) -> None:
    """Refuse a payload this build cannot read, before any field is decoded.

    Exactly two shapes are readable, and they are recognised by two different
    questions rather than by one falling back to the other:

    * a payload that **declares** a version is checked against
      ``OMS_SNAPSHOT_SCHEMA`` by the shared
      :func:`~alphalab.persistence.decode.require_schema_version`, so an
      unreadable, non-integer, zero or future version is refused by the same
      rule the portfolio and lifecycle snapshots use;
    * a payload that declares **no** version is readable only if its top-level
      keys are exactly :data:`LEGACY_UNVERSIONED_V0_KEYS`.

    Nothing here assigns a version to a payload that lacks one. The absence of
    ``schema_version`` is never treated as ``1``; it sends the payload to a
    structural test it either passes whole or fails. See ADR-0023.

    Raises:
        SnapshotDecodeError: If the declared version is not readable, or the
            payload is unversioned and is not the exact legacy shape.
    """

    if "schema_version" in payload:
        try:
            require_schema_version(payload, OMS_SNAPSHOT_SCHEMA, _SUBSYSTEM)
        except StateDecodeError as exc:
            # The rule is shared; the error type is this module's, because
            # SnapshotDecodeError is what every OMS decode failure raises and
            # what callers catch.
            raise SnapshotDecodeError(str(exc)) from exc
        return

    if frozenset(payload) == LEGACY_UNVERSIONED_V0_KEYS:
        return

    unexpected = sorted(frozenset(payload) - LEGACY_UNVERSIONED_V0_KEYS)
    absent = sorted(LEGACY_UNVERSIONED_V0_KEYS - frozenset(payload))
    raise SnapshotDecodeError(
        f"{_SUBSYSTEM} snapshot declares no schema_version and is not the "
        f"pre-v2.9 payload shape: it is missing {absent} and carries "
        f"{unexpected} that shape does not. An unversioned payload is read only "
        "when it matches that shape exactly; a missing schema_version is not "
        f"read as version {OMS_SNAPSHOT_SCHEMA}."
    )


def from_primitives(payload: Mapping[str, Any]) -> OMSSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`OMSSnapshot`.

    Raises:
        SnapshotDecodeError: If the payload is not an object, declares a schema
            version this build does not read, is an unversioned payload that is
            not the exact pre-v2.9 shape, is missing a field, or holds a value
            of the wrong type. The message names what did not match.
    """

    if not isinstance(payload, Mapping):
        raise SnapshotDecodeError(f"Snapshot payload is not an object: {payload!r}")

    _require_readable_shape(payload)

    return OMSSnapshot(
        orders=tuple(_order(order) for order in _sequence(payload, "orders")),
        active_orders=tuple(_order_id(oid) for oid in _sequence(payload, "active_orders")),
        completed_orders=tuple(_order_id(oid) for oid in _sequence(payload, "completed_orders")),
        history=tuple(
            OMSEventRecord(str(record["event_type"]), _event(record))
            for record in _sequence(payload, "history")
        ),
        events=tuple(
            OMSEventRecord(str(record["event_type"]), _event(record))
            for record in _sequence(payload, "events")
        ),
        schema_version=OMS_SNAPSHOT_SCHEMA,
    )
