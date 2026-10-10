"""The live-run envelope: a run snapshot and the venue binding beside it.

A live run is durable when **both halves** of it are. The run half has been
since v2.13 -- :mod:`alphalab.runtime.run_snapshot` over
:data:`~alphalab.runtime.run_snapshot.RUN_SNAPSHOT_SCHEMA`. The venue half
arrives in v2.16 as :mod:`alphalab.broker.snapshot`. This module is the envelope
that keeps them together, because keeping them apart is the failure:

* restore the run without the mapping and the restart re-sends every working
  order to a venue that already holds it;
* restore the mapping without the run and nothing knows which OMS order a
  returning fill belongs to.

**Two schema constants, not one, and the nesting is deliberate.**
:data:`LIVE_SNAPSHOT_SCHEMA` versions the *pairing*;
``RUN_SNAPSHOT_SCHEMA`` and ``BROKER_SNAPSHOT_SCHEMA`` version their own halves
and are unmoved by this module. That is the shape ADR-0030 decision 6 chose when
it put ``PIPELINE_SNAPSHOT_SCHEMA`` inside the run envelope rather than merging
them: a backtest payload written by this build is byte-identical to one written
before it, because the run envelope did not change. Adding broker fields to
``RunState`` instead would have moved a schema every driver shares in order to
serve the one driver that needs it.

What AlphaLab asked the venue, and what an algorithm sent
---------------------------------------------------------

Since schema 2 (v3.11) the envelope carries the run's request ledger
(:attr:`~alphalab.runtime.live.LiveRunState.requests`): every cancel and
amendment issued, in issue order. It is AlphaLab's record of what it asked --
not the venue's state, so it is not in the broker half -- and it is what tells a
retried request from a new one, which a restart must not forget.

The execution-algorithm bindings
(:attr:`~alphalab.runtime.live.LiveRunState.children`) are deliberately *not*
carried: every child handle names its parent and every mirror order records the
OMS order it represents, so :meth:`~alphalab.runtime.broker_routing.ChildOrderBindings.from_mirror`
rebuilds them exactly, in binding order, from the broker half. Carrying them too
would give one fact two homes -- the reason ``routed`` and ``settled`` are not
carried either.

========  =======  ==============================================================
Version   Release  What changed
========  =======  ==============================================================
1         v2.16    The run and broker halves, and the routing configuration.
2         v3.11    ``requests``: every cancel and amendment issued (ledger
                   BRK-003). Version 1 kept its ledger outside the envelope and
                   recorded none, so the upgrade records none.
========  =======  ==============================================================

What a restore does not do
--------------------------

It does not reconnect, and it does not reconcile. The restored
:class:`~alphalab.broker.state.BrokerState` is *what AlphaLab believed* when it
stopped; what the venue actually holds is a question only
:func:`~alphalab.broker.reconciliation.reconcile` against a fresh fetch answers.
A restore that silently reconnected would make the first health check report a
connection this process never opened.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.requests import CancelRequest, ModifyRequest, RequestLedger
from alphalab.broker.snapshot import (
    BrokerSnapshot,
)
from alphalab.broker.snapshot import (
    capture as capture_broker,
)
from alphalab.broker.snapshot import (
    from_primitives as broker_from_primitives,
)
from alphalab.broker.snapshot import (
    restore as restore_broker,
)
from alphalab.core.enums import OrderType
from alphalab.persistence.decode import as_mapping, as_sequence, require
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.upgrade import SchemaHistory, SchemaStep
from alphalab.runtime.broker_routing import ChildOrderBindings, RoutingConfig
from alphalab.runtime.live import LiveRunState
from alphalab.runtime.run_snapshot import (
    RunObjects,
    RunSnapshot,
)
from alphalab.runtime.run_snapshot import (
    capture as capture_run,
)
from alphalab.runtime.run_snapshot import (
    from_primitives as run_from_primitives,
)
from alphalab.runtime.run_snapshot import (
    restore as restore_run,
)

__all__ = [
    "LIVE_SCHEMA_HISTORY",
    "LIVE_SNAPSHOT_SCHEMA",
    "IssuedRequestRecord",
    "LiveRunSnapshot",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version of the *pairing*. The two halves carry their own; see the
#: module docstring for why they are nested rather than merged. Version 2
#: (v3.11) carries the request ledger.
LIVE_SNAPSHOT_SCHEMA: Final = 2

_SUBSYSTEM: Final = "live run"


@dataclass(frozen=True, slots=True)
class IssuedRequestRecord:
    """One issued cancel or amendment, tagged so it reads back as its own type."""

    kind: Literal["cancel", "modify"]
    request: CancelRequest | ModifyRequest


@dataclass(frozen=True, slots=True)
class LiveRunSnapshot:
    """Complete, JSON-serializable projection of a :class:`LiveRunState`.

    ``routed`` and ``settled`` -- the aggregate's own observability logs -- are
    **not** carried. Every fact in them is derivable from the two halves that
    are: the mapping says which orders reached the venue, and
    ``BrokerState.executions`` plus the reconciliation log say what every
    returning fill turned out to be. Carrying them again would give one fact two
    homes that could disagree after a restore, which is the defect
    ``source_id`` was consolidated to remove in v2.14.
    """

    run: RunSnapshot
    broker: BrokerSnapshot
    venue: str
    currency: str
    order_type: OrderType
    #: Every cancel and amendment the run issued, in issue order. See the
    #: module docstring.
    requests: tuple[IssuedRequestRecord, ...] = ()
    #: The OMS orders held for an execution algorithm, sorted (ledger LIV-001):
    #: a restored run that forgot one would send it whole.
    held: tuple[str, ...] = ()
    schema_version: int = LIVE_SNAPSHOT_SCHEMA


def _record(request: CancelRequest | ModifyRequest) -> IssuedRequestRecord:
    if isinstance(request, CancelRequest):
        return IssuedRequestRecord("cancel", request)
    return IssuedRequestRecord("modify", request)


def capture(state: LiveRunState) -> LiveRunSnapshot:
    """Project ``state`` into its complete serializable snapshot.

    The mapping and the reconciliation log are passed to the broker capture
    explicitly, because a live run without them is the restart that duplicates
    orders. See :func:`alphalab.broker.snapshot.capture`.
    """

    return LiveRunSnapshot(
        run=capture_run(state.run),
        broker=capture_broker(state.broker, state.mapping, state.reconciliation),
        venue=state.routing.venue,
        currency=state.routing.currency,
        order_type=state.routing.order_type,
        requests=tuple(_record(request) for request in state.requests.issued.values()),
        held=tuple(sorted(state.held)),
        schema_version=LIVE_SNAPSHOT_SCHEMA,
    )


def _ledger(records: Iterable[IssuedRequestRecord]) -> RequestLedger:
    """The ledger the records were issued into, rebuilt in issue order.

    The per-order latest attempt and revision are derived rather than carried:
    the ledger only ever issues a number at or above the latest one, so the
    latest is the highest issued.

    Raises:
        StateDecodeError: If one request appears twice. The ledger is keyed by
            request identity, so the payload did not come from one.
    """

    ledger = RequestLedger()
    for record in records:
        request = record.request
        request_id = request.request_id
        if request_id in ledger.issued:
            raise StateDecodeError(
                f"live run snapshot issues request {request_id} twice; a ledger holds each "
                "request once."
            )
        issued = ledger.issued.set(request_id, request)
        order = request.broker_order_id
        if isinstance(request, CancelRequest):
            latest = ledger.latest_cancel.get(order)
            if latest is None or request.attempt > latest[0]:
                ledger = RequestLedger(
                    issued,
                    ledger.latest_cancel.set(order, (request.attempt, request_id)),
                    ledger.latest_revision,
                )
            else:
                ledger = RequestLedger(issued, ledger.latest_cancel, ledger.latest_revision)
        else:
            latest = ledger.latest_revision.get(order)
            if latest is None or request.revision > latest[0]:
                ledger = RequestLedger(
                    issued,
                    ledger.latest_cancel,
                    ledger.latest_revision.set(order, (request.revision, request_id)),
                )
            else:
                ledger = RequestLedger(issued, ledger.latest_cancel, ledger.latest_revision)
    return ledger


def restore(snapshot: LiveRunSnapshot, objects: RunObjects) -> LiveRunState:
    """Rebuild the live run a snapshot was captured from.

    ``objects`` carries the live objects no payload can hold -- strategy
    instances, the sizing model, the simulator, the instrument registry, the
    fill policy. **The broker adapter is not among them**: it is supplied to
    each :class:`~alphalab.runtime.live.LiveSession` call, not held in state, so
    a restored run takes whichever adapter the new process constructs.

    Returns:
        The reconstructed live run, with its request ledger and -- rebuilt from
        the mirror -- its execution-algorithm bindings. Nothing is connected and
        nothing is reconciled; continuing is
        :meth:`~alphalab.runtime.live.LiveSession.resume` plus
        :meth:`~alphalab.runtime.live.LiveSession.connect`.
    """

    broker_state, mapping, log = restore_broker(snapshot.broker)
    return LiveRunState(
        run=restore_run(snapshot.run, objects),
        broker=broker_state,
        mapping=mapping,
        reconciliation=log,
        routing=RoutingConfig(
            venue=snapshot.venue,
            currency=snapshot.currency,
            order_type=snapshot.order_type,
        ),
        children=ChildOrderBindings.from_mirror(broker_state),
        requests=_ledger(snapshot.requests),
        held=frozenset(snapshot.held),
    )


def _order_type(value: Any) -> OrderType:
    """Decode an ``OrderType``, which is a ``StrEnum`` and serializes by value.

    Both shapes are read -- ``"market"`` as ``alphalab.persistence.serialize``
    writes it, and ``"MARKET"`` as a hand-written payload would -- and anything
    else is refused rather than defaulted to ``MARKET``. Routing an order as the
    wrong type is a real instruction to a real venue.
    """

    raw = str(value)
    member = OrderType.__members__.get(raw)
    if member is not None:
        return member
    try:
        return OrderType(raw)
    except ValueError:
        raise StateDecodeError(
            f"live run snapshot order_type {value!r} is not an OrderType. "
            f"Known: {sorted(OrderType.__members__)}"
        ) from None


def _decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise StateDecodeError(f"live run snapshot {field_name} is not a decimal: {value!r}")
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        raise StateDecodeError(
            f"live run snapshot {field_name} is not a decimal: {value!r}"
        ) from None


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StateDecodeError(f"live run snapshot {field_name} is not an integer: {value!r}")
    return value


def _instant(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StateDecodeError(f"live run snapshot {field_name} is not an instant: {value!r}")
    return float(value)


def _request_record(entry: Any) -> IssuedRequestRecord:
    record = as_mapping(entry, "request")
    kind = require(record, "kind")
    data = as_mapping(require(record, "request"), "request")
    try:
        if kind == "cancel":
            return IssuedRequestRecord(
                "cancel",
                CancelRequest(
                    broker_order_id=str(require(data, "broker_order_id")),
                    attempt=_integer(require(data, "attempt"), "request.attempt"),
                    requested_at=_instant(require(data, "requested_at"), "request.requested_at"),
                ),
            )
        if kind == "modify":
            return IssuedRequestRecord(
                "modify",
                ModifyRequest(
                    broker_order_id=str(require(data, "broker_order_id")),
                    revision=_integer(require(data, "revision"), "request.revision"),
                    quantity=_decimal(require(data, "quantity"), "request.quantity"),
                    price=_decimal(require(data, "price"), "request.price"),
                    requested_at=_instant(require(data, "requested_at"), "request.requested_at"),
                ),
            )
    except BrokerValidationError as exc:
        raise StateDecodeError(f"live run snapshot holds an invalid request: {exc}") from exc
    raise StateDecodeError(
        f"live run snapshot request kind {kind!r} is neither 'cancel' nor 'modify'."
    )


def _requests(payload: Mapping[str, Any]) -> tuple[IssuedRequestRecord, ...]:
    records = tuple(
        _request_record(entry) for entry in as_sequence(require(payload, "requests"), "requests")
    )
    _ledger(records)  # refuses a request issued twice, here rather than at restore
    return records


def _held(payload: Mapping[str, Any]) -> tuple[str, ...]:
    entries = as_sequence(require(payload, "held"), "held")
    if any(not isinstance(entry, str) for entry in entries):
        raise StateDecodeError("live run snapshot 'held' must list OMS order ids as strings.")
    held = tuple(entries)
    if len(set(held)) != len(held):
        raise StateDecodeError("live run snapshot holds an order for an algorithm twice.")
    return held


def _v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Version 2 carries the request ledger and the held orders; version 1 had neither."""

    return {**payload, "requests": [], "held": []}


#: How every live run payload a release has written is read by this one. See
#: :mod:`alphalab.persistence.upgrade`.
LIVE_SCHEMA_HISTORY = SchemaHistory(
    _SUBSYSTEM,
    LIVE_SNAPSHOT_SCHEMA,
    (
        SchemaStep(
            1,
            "version 2 carries every cancel and amendment the run issued, and the orders held "
            "for an execution algorithm; version 1 kept its ledger outside the envelope and "
            "recorded neither",
            upgrade=_v1_to_v2,
        ),
    ),
)


def from_primitives(payload: Mapping[str, Any]) -> LiveRunSnapshot:
    """Decode a JSON-decoded payload back into :class:`LiveRunSnapshot`.

    Each half is decoded by its own module, so each checks its own schema
    version and produces its own message. A payload whose halves were written by
    different builds is refused by whichever half disagrees first.

    Raises:
        StateDecodeError: If the envelope declares a version this build does not
            read, is missing a field, names an order type that does not exist, or
            holds a request that is malformed or issued twice.
    """

    payload = as_mapping(payload, _SUBSYSTEM)
    payload = LIVE_SCHEMA_HISTORY.upgrade(payload)

    return LiveRunSnapshot(
        run=run_from_primitives(as_mapping(require(payload, "run"), "run")),
        broker=broker_from_primitives(as_mapping(require(payload, "broker"), "broker")),
        venue=str(require(payload, "venue")),
        currency=str(require(payload, "currency")),
        order_type=_order_type(require(payload, "order_type")),
        requests=_requests(payload),
        held=_held(payload),
        schema_version=LIVE_SNAPSHOT_SCHEMA,
    )
