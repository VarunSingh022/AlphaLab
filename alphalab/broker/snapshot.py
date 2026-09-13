"""Complete, restorable snapshots of the venue side of a live run.

A run's own state has been durable since v2.13:
:class:`~alphalab.persistence.run_store.RunStateStore` holds it and
:mod:`alphalab.runtime.run_snapshot` projects it. The venue side had **no
snapshot at all** -- ``grep "def capture" alphalab/broker/`` returned nothing
through v2.15 -- so a live run that stopped lost the one fact a restart must
never guess.

Why this is the fact that matters
---------------------------------

:class:`~alphalab.broker.reconciliation.ExternalOrderMap` is the single answer to
"has this order already been sent?", and
:func:`~alphalab.runtime.broker_routing.route_order` refuses a
``DUPLICATE_SUBMISSION`` on its evidence alone. Lose it and a restarted run sees
its own working orders as unrouted and sends every one of them again -- to a
venue that already holds them. The duplicate-submission gate is worth exactly as
much as the mapping's durability, which until now was zero across a process
boundary.

``BrokerState.executions`` carries the same weight in the other direction: it is
what makes a redelivered fill a ``DUPLICATE`` rather than a second fill. A
restart that forgot it would double-count every execution the venue replays.

What is captured, and what is deliberately not
----------------------------------------------

Captured: the connection's identity and status, the account and positions the
venue last reported, every order AlphaLab believes the venue holds, every
execution already applied, the order mapping, and the reconciliation log.

**Not captured: the adapter.** A :class:`~alphalab.broker.protocol.BrokerProtocol`
is a live object holding a socket, credentials and a transport, and none of
those survive a process. :func:`restore` therefore takes the state and the
caller supplies a freshly constructed adapter -- the rule ADR-0023 set for every
live object, and the one
:func:`alphalab.runtime.run_snapshot.restore` already follows for strategy
instances and registries.

**Not captured: the venue's truth.** This is what *AlphaLab believed* when it
stopped. A restart that wants to know what the venue actually holds calls
:func:`~alphalab.broker.reconciliation.reconcile` against a fresh fetch, which
is the function that exists for exactly that question. Restoring is not
reconciling, and this module does not pretend otherwise.

Schema version
--------------

:data:`BROKER_SNAPSHOT_SCHEMA` is a module-local literal rather than
``DEFAULT_SCHEMA_VERSION``, for the reason v2.6 gave for the portfolio and v2.8
for the lifecycle: that constant also versions ``BaseEvent``, so bumping it
would version every event in the system as a side effect of one subsystem's
change. There is no unversioned shape to recognise here -- nothing before v2.16
wrote one -- so a payload without ``schema_version`` is refused outright.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Final

from alphalab.broker.account import BrokerAccount
from alphalab.broker.events import (
    BrokerConnected,
    BrokerDisconnected,
    BrokerEvent,
    ExecutionReceived,
    Heartbeat,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    OrderSubmitted,
)
from alphalab.broker.exceptions import BrokerError
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import (
    ExecutionDecision,
    ExecutionOutcome,
    ExternalOrderMap,
    ReconciliationLog,
)
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import AssetType, OrderType, Side, TimeInForce
from alphalab.core.enums import OrderStatus as CoreOrderStatus
from alphalab.persistence.decode import require_schema_version

__all__ = [
    "BROKER_SNAPSHOT_SCHEMA",
    "BrokerEventRecord",
    "BrokerSnapshot",
    "BrokerSnapshotDecodeError",
    "capture",
    "from_primitives",
    "restore",
]


class BrokerSnapshotDecodeError(BrokerError):
    """Raised when a snapshot payload cannot be read back into broker types."""


#: Schema version this module reads and writes. New in v2.16; see ADR-0033.
BROKER_SNAPSHOT_SCHEMA: Final = 1

_SUBSYSTEM: Final = "broker"

#: Every broker event type, by the tag written into a snapshot. A heterogenous
#: event log cannot be read back into typed events without one.
_EVENT_TYPES: Mapping[str, type[BrokerEvent]] = {
    cls.__name__: cls
    for cls in (
        BrokerConnected,
        BrokerDisconnected,
        OrderSubmitted,
        OrderAccepted,
        OrderRejected,
        OrderCancelled,
        ExecutionReceived,
        Heartbeat,
    )
}


@dataclass(frozen=True, slots=True)
class BrokerEventRecord:
    """One broker event plus the tag needed to read it back as its own type."""

    event_type: str
    event: BrokerEvent


@dataclass(frozen=True, slots=True)
class BrokerSnapshot:
    """Complete, JSON-serializable projection of the venue side of a run.

    ``orders``, ``positions`` and ``executions`` serialize as **arrays** rather
    than as the mappings they are held in: every one of them is keyed by a field
    the value already carries, so the key restates the value and
    :func:`restore` rebuilds the index exactly. This is the projection
    :mod:`alphalab.oms.snapshot` established for the order book.
    """

    broker_name: str
    connection_status: ConnectionStatus
    account: BrokerAccount
    positions: tuple[BrokerPosition, ...] = ()
    orders: tuple[BrokerOrder, ...] = ()
    executions: tuple[BrokerExecution, ...] = ()
    events: tuple[BrokerEventRecord, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)
    last_heartbeat: float = 0.0
    #: ``oms_order_id`` -> ``broker_order_id``. The reverse direction is derived
    #: on restore, so the two cannot be captured disagreeing with each other.
    order_bindings: Mapping[str, str] = field(default_factory=dict)
    breaks: tuple[ExecutionDecision, ...] = ()
    duplicates: tuple[ExecutionDecision, ...] = ()
    schema_version: int = BROKER_SNAPSHOT_SCHEMA


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture(
    state: BrokerState,
    mapping: ExternalOrderMap | None = None,
    log: ReconciliationLog | None = None,
) -> BrokerSnapshot:
    """Project the venue side of a run into its complete serializable snapshot.

    ``mapping`` and ``log`` are optional so that a caller holding only a
    :class:`~alphalab.broker.state.BrokerState` can capture it, but a *live run*
    must pass both: without the mapping a restart re-sends orders the venue
    already holds. :class:`~alphalab.runtime.live_snapshot.LiveRunSnapshot`
    passes them and is what a live run should use.
    """

    bindings = dict(mapping.to_broker) if mapping is not None else {}
    reconciliation = log if log is not None else ReconciliationLog()

    return BrokerSnapshot(
        broker_name=state.broker_name,
        connection_status=state.connection_status,
        account=state.account,
        positions=tuple(state.positions.values()),
        orders=tuple(state.orders.values()),
        executions=tuple(state.executions.values()),
        events=tuple(BrokerEventRecord(type(event).__name__, event) for event in state.events),
        metadata=dict(state.metadata),
        last_heartbeat=state.last_heartbeat,
        order_bindings=bindings,
        breaks=reconciliation.breaks,
        duplicates=reconciliation.duplicates,
        schema_version=BROKER_SNAPSHOT_SCHEMA,
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(snapshot: BrokerSnapshot) -> tuple[BrokerState, ExternalOrderMap, ReconciliationLog]:
    """Rebuild the three values a snapshot was captured from.

    The connection status is restored **as captured**, not reset to
    ``DISCONNECTED``. A restored state describes what AlphaLab believed, and
    quietly rewriting it would make a resumed run's first health check read
    "connected" or "disconnected" for a reason that came from this function
    rather than from the venue. A caller reconnects, and the adapter reports.
    """

    positions: PersistentMap[str, BrokerPosition] = PersistentMap()
    for position in snapshot.positions:
        positions = positions.set(position.symbol, position)

    orders: PersistentMap[str, BrokerOrder] = PersistentMap()
    for order in snapshot.orders:
        orders = orders.set(order.broker_order_id, order)

    executions: PersistentMap[str, BrokerExecution] = PersistentMap()
    for execution in snapshot.executions:
        executions = executions.set(execution.execution_id, execution)

    mapping = ExternalOrderMap()
    for oms_order_id, broker_order_id in snapshot.order_bindings.items():
        mapping = mapping.bind(oms_order_id, broker_order_id)

    state = BrokerState(
        broker_name=snapshot.broker_name,
        connection_status=snapshot.connection_status,
        account=snapshot.account,
        positions=positions,
        orders=orders,
        executions=executions,
        events=AppendOnlyLog(record.event for record in snapshot.events),
        metadata=dict(snapshot.metadata),
        last_heartbeat=snapshot.last_heartbeat,
    )
    log = ReconciliationLog(breaks=snapshot.breaks, duplicates=snapshot.duplicates)
    return state, mapping, log


# ---------------------------------------------------------------------------
# Decoding a JSON payload back into snapshot types
# ---------------------------------------------------------------------------


def _require(payload: Mapping[str, Any], key: str) -> Any:
    if key not in payload:
        raise BrokerSnapshotDecodeError(f"Broker snapshot payload is missing {key!r}")
    return payload[key]


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise BrokerSnapshotDecodeError(f"{field_name} is not a decimal: {value!r}") from exc


def _sequence(payload: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = _require(payload, key)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise BrokerSnapshotDecodeError(f"{key} is not an array: {value!r}")
    return value


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = _require(payload, key)
    if not isinstance(value, Mapping):
        raise BrokerSnapshotDecodeError(f"{key} is not an object: {value!r}")
    return value


def _enum[T: Enum](cls: type[T], value: Any, field_name: str) -> T:
    """Decode an enum, in the three shapes this codec writes.

    ``alphalab.persistence.serialize`` writes a ``StrEnum`` by *value*
    (``"market"``), because ``json`` encodes it natively, and a plain ``Enum`` by
    ``str(member)`` (``"ConnectionStatus.CONNECTED"``). Both appear in a broker
    payload, so both are read -- and a bare member name is read too, because that
    is what a hand-written payload looks like.

    Unknown values are refused, never defaulted: a connection status nobody can
    decode is the one thing a restored live run must not guess at.
    """

    raw = str(value)
    for candidate in (raw, raw.rpartition(".")[2]):
        member = cls.__members__.get(candidate)
        if member is not None:
            return member
    try:
        return cls(raw)
    except ValueError:
        pass
    raise BrokerSnapshotDecodeError(
        f"{field_name} is not a {cls.__name__}: {value!r}. Known: {sorted(cls.__members__)}"
    )


def _order_status(value: Any) -> CoreOrderStatus | BrokerOrderStatus:
    """An order's status is canonical *or* broker-local. Both are read.

    The two vocabularies are disjoint by construction --
    :class:`~alphalab.broker.order.BrokerOrderStatus` holds exactly the states
    that exist only between AlphaLab and a venue, and ``SUBMITTED`` is
    deliberately not a canonical lifecycle state -- so a value resolves to one
    of them and never to both. The canonical one is tried first because it is
    the one the execution path reads.
    """

    try:
        return _enum(CoreOrderStatus, value, "order.status")
    except BrokerSnapshotDecodeError:
        pass
    try:
        return _enum(BrokerOrderStatus, value, "order.status")
    except BrokerSnapshotDecodeError:
        pass
    raise BrokerSnapshotDecodeError(
        f"order.status {value!r} is neither a canonical OrderStatus nor a "
        f"BrokerOrderStatus. Known: {sorted(CoreOrderStatus.__members__)} and "
        f"{sorted(BrokerOrderStatus.__members__)}."
    )


def _account(payload: Any) -> BrokerAccount:
    data = payload if isinstance(payload, Mapping) else {}
    if not data:
        raise BrokerSnapshotDecodeError(f"account is not an object: {payload!r}")
    return BrokerAccount(
        account_id=str(_require(data, "account_id")),
        cash=_decimal(_require(data, "cash"), "account.cash"),
        equity=_decimal(_require(data, "equity"), "account.equity"),
        buying_power=_decimal(_require(data, "buying_power"), "account.buying_power"),
        margin=_decimal(_require(data, "margin"), "account.margin"),
        available_funds=_decimal(_require(data, "available_funds"), "account.available_funds"),
        currency=str(_require(data, "currency")),
        broker_id=str(data.get("broker_id", "")),
        metadata={str(k): str(v) for k, v in dict(data.get("metadata", {})).items()},
    )


def _position(payload: Any) -> BrokerPosition:
    data = payload if isinstance(payload, Mapping) else {}
    if not data:
        raise BrokerSnapshotDecodeError(f"position is not an object: {payload!r}")
    return BrokerPosition(
        symbol=str(_require(data, "symbol")),
        quantity=_decimal(_require(data, "quantity"), "position.quantity"),
        average_price=_decimal(_require(data, "average_price"), "position.average_price"),
        market_value=_decimal(_require(data, "market_value"), "position.market_value"),
        unrealized_pnl=_decimal(_require(data, "unrealized_pnl"), "position.unrealized_pnl"),
        realized_pnl=_decimal(_require(data, "realized_pnl"), "position.realized_pnl"),
        account_id=str(data.get("account_id", "")),
        asset_class=_enum(AssetType, data.get("asset_class", "EQUITY"), "position.asset_class"),
        market_price=_decimal(data.get("market_price", "0"), "position.market_price"),
    )


def _order(payload: Any) -> BrokerOrder:
    data = payload if isinstance(payload, Mapping) else {}
    if not data:
        raise BrokerSnapshotDecodeError(f"order is not an object: {payload!r}")
    return BrokerOrder(
        broker_order_id=str(_require(data, "broker_order_id")),
        oms_order_id=str(_require(data, "oms_order_id")),
        symbol=str(_require(data, "symbol")),
        side=_enum(Side, _require(data, "side"), "order.side"),
        order_type=_enum(OrderType, _require(data, "order_type"), "order.order_type"),
        quantity=_decimal(_require(data, "quantity"), "order.quantity"),
        price=_decimal(_require(data, "price"), "order.price"),
        filled_quantity=_decimal(_require(data, "filled_quantity"), "order.filled_quantity"),
        average_fill_price=_decimal(
            _require(data, "average_fill_price"), "order.average_fill_price"
        ),
        status=_order_status(_require(data, "status")),
        created_at=float(_require(data, "created_at")),
        updated_at=float(_require(data, "updated_at")),
        account_id=str(data.get("account_id", "")),
        tif=_enum(TimeInForce, data.get("tif", "DAY"), "order.tif"),
        stop_price=_decimal(data.get("stop_price", "0"), "order.stop_price"),
    )


def _execution(payload: Any) -> BrokerExecution:
    data = payload if isinstance(payload, Mapping) else {}
    if not data:
        raise BrokerSnapshotDecodeError(f"execution is not an object: {payload!r}")
    return BrokerExecution(
        execution_id=str(_require(data, "execution_id")),
        broker_order_id=str(_require(data, "broker_order_id")),
        symbol=str(_require(data, "symbol")),
        fill_quantity=_decimal(_require(data, "fill_quantity"), "execution.fill_quantity"),
        fill_price=_decimal(_require(data, "fill_price"), "execution.fill_price"),
        commission=_decimal(_require(data, "commission"), "execution.commission"),
        timestamp=float(_require(data, "timestamp")),
        account_id=str(data.get("account_id", "")),
        external_id=str(data.get("external_id", "")),
    )


def _event(record: Any) -> BrokerEvent:
    data = record if isinstance(record, Mapping) else {}
    tag = str(_require(data, "event_type"))
    cls = _EVENT_TYPES.get(tag)
    if cls is None:
        raise BrokerSnapshotDecodeError(
            f"Unknown broker event type {tag!r}. Known: {sorted(_EVENT_TYPES)}"
        )
    payload = _mapping(data, "event")
    known = {f.name for f in cls.__dataclass_fields__.values()}
    try:
        return cls(**{k: v for k, v in payload.items() if k in known})
    except Exception as exc:
        raise BrokerSnapshotDecodeError(f"{tag} could not be decoded: {payload!r}") from exc


def _decision(payload: Any) -> ExecutionDecision:
    data = payload if isinstance(payload, Mapping) else {}
    if not data:
        raise BrokerSnapshotDecodeError(f"decision is not an object: {payload!r}")
    return ExecutionDecision(
        outcome=_enum(ExecutionOutcome, _require(data, "outcome"), "decision.outcome"),
        execution=_execution(_require(data, "execution")),
        reason=str(_require(data, "reason")),
    )


def from_primitives(payload: Mapping[str, Any]) -> BrokerSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`BrokerSnapshot`.

    Raises:
        BrokerSnapshotDecodeError: If the payload is not an object, is missing a
            field, or holds a value of the wrong type.
        StateDecodeError: If it declares a schema version this build does not
            read. There is no migration path and no unversioned shape.
    """

    if not isinstance(payload, Mapping):
        raise BrokerSnapshotDecodeError(f"Broker snapshot payload is not an object: {payload!r}")

    require_schema_version(payload, BROKER_SNAPSHOT_SCHEMA, _SUBSYSTEM)

    return BrokerSnapshot(
        broker_name=str(_require(payload, "broker_name")),
        connection_status=_enum(
            ConnectionStatus, _require(payload, "connection_status"), "connection_status"
        ),
        account=_account(_require(payload, "account")),
        positions=tuple(_position(entry) for entry in _sequence(payload, "positions")),
        orders=tuple(_order(entry) for entry in _sequence(payload, "orders")),
        executions=tuple(_execution(entry) for entry in _sequence(payload, "executions")),
        events=tuple(
            BrokerEventRecord(str(record["event_type"]), _event(record))
            for record in _sequence(payload, "events")
        ),
        metadata={str(k): str(v) for k, v in _mapping(payload, "metadata").items()},
        last_heartbeat=float(_require(payload, "last_heartbeat")),
        order_bindings={str(k): str(v) for k, v in _mapping(payload, "order_bindings").items()},
        breaks=tuple(_decision(entry) for entry in _sequence(payload, "breaks")),
        duplicates=tuple(_decision(entry) for entry in _sequence(payload, "duplicates")),
        schema_version=BROKER_SNAPSHOT_SCHEMA,
    )
