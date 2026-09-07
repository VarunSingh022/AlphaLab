"""Complete, restorable snapshots of an :class:`~alphalab.allocation.state.AllocationState`.

Why allocation needs one of its own
-----------------------------------
``AllocationState`` owns the two ledgers that decide whether a run's committed
capital and its attribution are correct: ``reservations``, the per-order record
of capital still held, and ``contributions``, the parallel record of which
strategies asked for each order. ADR-0021 made their lifetime exact -- a
contribution exists exactly as long as its request does -- and ADR-0024 gave a
restored working order a way to end. Neither guarantee survives a round trip the
ledgers do not, which is why allocation gets a versioned snapshot rather than
riding inline in a larger envelope.

What the projection changes, and why
------------------------------------
Only what JSON cannot carry directly:

* ``events`` is a heterogeneous log, so every event is wrapped in an
  :class:`AllocationEventRecord` carrying an explicit ``event_type`` tag. Without
  it a decoder cannot know which event class a payload describes -- the same
  reason :mod:`alphalab.oms.snapshot` and :mod:`alphalab.portfolio.snapshot` tag
  theirs.
* ``reservations`` and ``contributions`` are
  :class:`~alphalab.common.persistent_map.PersistentMap` views. They project as
  plain mappings and :func:`restore` rebuilds the persistent form, exactly as the
  portfolio's cash balances do. Structural sharing is not reproduced and does not
  need to be: ADR-0014 settled that a restored value compares equal without
  reproducing container lineage.
* ``history`` projects as an array of :class:`~alphalab.core.order_request.OrderRequest`,
  which it already is.

``budget`` is carried verbatim. It is *configuration* in role -- ADR-0015
decision 1 keeps ``CapitalBudget`` a sizing parameter and puts commitment in
``notional_allocated`` -- but it is pure data in form, an immutable dataclass of
decimals, so it is captured rather than required back from the caller. The
reference-and-rebind rule ADR-0014 sets exists for objects that *cannot* be
reconstructed from data, and this is not one.

Nothing here mints an identifier. Every id in a restored state is the id the
captured state held, and decoding never touches the ambient identifier source --
the stream position is a separate concern with a separate owner (ADR-0022).

Schema version
--------------
Every payload carries ``schema_version``, and a missing or unreadable version is
refused. There is no legacy path and deliberately so: allocation has never had a
``capture``, a ``restore`` or a ``from_primitives``, so while ``serialize`` would
encode the state incidentally -- as it does for market, risk and execution state
-- nothing could ever read one back. A format with no reader has no compatibility
surface to preserve, which is why this differs from
:mod:`alphalab.oms.snapshot`, whose JSON round trip was a documented public
recipe. See ADR-0023.

Round trip
----------
::

    payload = serialize(capture(state))
    assert restore(from_primitives(deserialize(payload))) == state
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any, Final

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.events import (
    AllocationCompleted,
    AllocationEvent,
    AllocationExecutionApplied,
    AllocationRejected,
    AllocationReservationReleased,
    AllocationStarted,
    BudgetExceeded,
    NettingCompleted,
)
from alphalab.allocation.state import AllocationState
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import Side
from alphalab.core.order_request import OrderRequest
from alphalab.persistence.decode import (
    as_decimal,
    as_decimal_mapping,
    as_float,
    as_int,
    as_mapping,
    as_sequence,
    as_str,
    as_value_enum,
    require,
    require_schema_version,
)
from alphalab.persistence.exceptions import StateDecodeError

__all__ = [
    "ALLOCATION_SNAPSHOT_SCHEMA",
    "AllocationEventRecord",
    "AllocationSnapshot",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version this module reads and writes. See ADR-0023.
#:
#: A module-local literal rather than ``DEFAULT_SCHEMA_VERSION``, for the reason
#: v2.6 gave for the portfolio and v2.8 for the lifecycle: that constant also
#: versions ``CommonEvent`` and ``BaseEvent``, so bumping it would version every
#: event in the system as a side effect of one subsystem's change.
ALLOCATION_SNAPSHOT_SCHEMA: Final = 1

_SUBSYSTEM: Final = "allocation"

#: Every allocation event type, by the tag written into a snapshot.
_EVENT_TYPES: Mapping[str, type[AllocationEvent]] = {
    cls.__name__: cls
    for cls in (
        AllocationStarted,
        AllocationCompleted,
        NettingCompleted,
        BudgetExceeded,
        AllocationRejected,
        AllocationExecutionApplied,
        AllocationReservationReleased,
    )
}

#: How to decode each event field that is not already a JSON string. Every
#: allocation event derives from ``BaseEvent``, so all of them carry
#: ``event_id`` and ``timestamp``; the rest differ per event.
_EVENT_FIELD_DECODERS: Mapping[str, Any] = {
    "timestamp": as_float,
    "num_intents": as_int,
    "num_orders_generated": as_int,
    "total_notional": as_decimal,
    "net_quantity": as_decimal,
    "requested_notional": as_decimal,
    "available_budget": as_decimal,
    "executed_notional": as_decimal,
    "released_notional": as_decimal,
}


@dataclass(frozen=True, slots=True)
class AllocationEventRecord:
    """One allocation event plus the tag needed to read it back as its own type."""

    event_type: str
    event: AllocationEvent


@dataclass(frozen=True, slots=True)
class AllocationSnapshot:
    """Complete, JSON-serializable projection of an :class:`AllocationState`."""

    budget: CapitalBudget
    history: tuple[OrderRequest, ...]
    events: tuple[AllocationEventRecord, ...]
    notional_allocated: Decimal
    reservations: Mapping[str, Decimal]
    contributions: Mapping[str, tuple[StrategyContribution, ...]]
    schema_version: int = ALLOCATION_SNAPSHOT_SCHEMA


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture(state: AllocationState) -> AllocationSnapshot:
    """Project ``state`` into its complete serializable snapshot."""

    return AllocationSnapshot(
        budget=state.budget,
        history=state.history.to_tuple(),
        events=tuple(AllocationEventRecord(type(event).__name__, event) for event in state.events),
        notional_allocated=state.notional_allocated,
        reservations=dict(state.reservations),
        contributions=dict(state.contributions),
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(snapshot: AllocationSnapshot) -> AllocationState:
    """Rebuild the state a snapshot was captured from.

    The persistent ledgers are rebuilt from the projected mappings, so a restored
    state indexes and iterates exactly as the captured one did. Its internal
    version chains are its own -- ADR-0014's contract is value equality, not
    lineage.
    """

    return AllocationState(
        budget=snapshot.budget,
        history=AppendOnlyLog(snapshot.history),
        events=AppendOnlyLog(record.event for record in snapshot.events),
        notional_allocated=snapshot.notional_allocated,
        reservations=PersistentMap(snapshot.reservations),
        contributions=PersistentMap(snapshot.contributions),
    )


# ---------------------------------------------------------------------------
# Decoding a JSON payload back into snapshot types
# ---------------------------------------------------------------------------


def _budget(value: Any) -> CapitalBudget:
    payload = as_mapping(value, "budget")
    return CapitalBudget(
        global_capital=as_decimal(require(payload, "global_capital"), "budget.global_capital"),
        maximum_exposure=as_decimal(
            require(payload, "maximum_exposure"), "budget.maximum_exposure"
        ),
        cash_buffer=as_decimal(require(payload, "cash_buffer"), "budget.cash_buffer"),
        strategy_budgets=as_decimal_mapping(
            require(payload, "strategy_budgets"), "budget.strategy_budgets"
        ),
    )


def _contribution(value: Any, where: str) -> StrategyContribution:
    payload = as_mapping(value, where)
    return StrategyContribution(
        strategy_id=as_str(require(payload, "strategy_id"), f"{where}.strategy_id"),
        quantity=as_decimal(require(payload, "quantity"), f"{where}.quantity"),
    )


def _contributions(value: Any, where: str) -> tuple[StrategyContribution, ...]:
    return tuple(
        _contribution(item, f"{where}[{index}]")
        for index, item in enumerate(as_sequence(value, where))
    )


def _order_request(value: Any, index: int) -> OrderRequest:
    where = f"history[{index}]"
    payload = as_mapping(value, where)
    return OrderRequest(
        order_id=as_str(require(payload, "order_id"), f"{where}.order_id"),
        strategy_id=as_str(require(payload, "strategy_id"), f"{where}.strategy_id"),
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        side=as_value_enum(Side, require(payload, "side"), f"{where}.side"),
        quantity=as_decimal(require(payload, "quantity"), f"{where}.quantity"),
        price=as_decimal(require(payload, "price"), f"{where}.price"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        contributions=_contributions(require(payload, "contributions"), f"{where}.contributions"),
    )


def _event(value: Any, index: int) -> AllocationEventRecord:
    where = f"events[{index}]"
    record = as_mapping(value, where)
    event_type = as_str(require(record, "event_type"), f"{where}.event_type")
    cls = _EVENT_TYPES.get(event_type)
    if cls is None:
        known = ", ".join(sorted(_EVENT_TYPES))
        raise StateDecodeError(
            f"{where}.event_type is not an allocation event: {event_type!r}; "
            f"expected one of {known}"
        )

    payload = as_mapping(require(record, "event"), f"{where}.event")
    kwargs: dict[str, Any] = {}
    for field in fields(cls):
        raw = require(payload, field.name)
        decoder = _EVENT_FIELD_DECODERS.get(field.name)
        kwargs[field.name] = (
            decoder(raw, f"{where}.{field.name}")
            if decoder is not None
            else as_str(raw, f"{where}.{field.name}")
        )
    return AllocationEventRecord(event_type, cls(**kwargs))


def _indexed(payload: Mapping[str, Any], key: str) -> Sequence[Any]:
    return as_sequence(require(payload, key), key)


def _ledger(payload: Mapping[str, Any]) -> dict[str, tuple[StrategyContribution, ...]]:
    contributions = as_mapping(require(payload, "contributions"), "contributions")
    return {
        as_str(order_id, "contributions key"): _contributions(value, f"contributions[{order_id}]")
        for order_id, value in contributions.items()
    }


def from_primitives(payload: Mapping[str, Any]) -> AllocationSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`AllocationSnapshot`.

    Raises:
        StateDecodeError: If the payload is not an object, declares no schema
            version or one this build does not read, is missing a field, or holds
            a value of the wrong type. The message names the field.
    """

    payload = as_mapping(payload, "allocation snapshot")
    require_schema_version(payload, ALLOCATION_SNAPSHOT_SCHEMA, _SUBSYSTEM)

    return AllocationSnapshot(
        budget=_budget(require(payload, "budget")),
        history=tuple(
            _order_request(item, index) for index, item in enumerate(_indexed(payload, "history"))
        ),
        events=tuple(_event(item, index) for index, item in enumerate(_indexed(payload, "events"))),
        notional_allocated=as_decimal(require(payload, "notional_allocated"), "notional_allocated"),
        reservations=as_decimal_mapping(require(payload, "reservations"), "reservations"),
        contributions=_ledger(payload),
        schema_version=ALLOCATION_SNAPSHOT_SCHEMA,
    )
