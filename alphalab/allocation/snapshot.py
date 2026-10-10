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
from dataclasses import dataclass, field, fields
from decimal import Decimal
from typing import Any, Final

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.ceilings import StrategyCapital
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
    MARKET_TERMS_PAYLOAD,
    as_bool,
    as_decimal,
    as_decimal_mapping,
    as_float,
    as_int,
    as_mapping,
    as_order_terms,
    as_sequence,
    as_str,
    as_value_enum,
    require,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.upgrade import SchemaHistory, SchemaStep

__all__ = [
    "ALLOCATION_SCHEMA_HISTORY",
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
#: versions ``BaseEvent``, so bumping it would version every
#: event in the system as a side effect of one subsystem's change.
#:
#: Version 3 (v3.12) records whether the budget enforces per-strategy ceilings
#: and each ceilinged strategy's committed capital (ledger OFE-003), and reads
#: the budget's ``currency`` back. Every version since v2.17 wrote it and none
#: read it, so a restored allocation held an unstated budget whatever the
#: captured one said -- ``restore(capture(state)) != state`` -- and a re-capture
#: rewrote the payload (ledger PER-006). No figure changed: the pipeline reads
#: its own configuration's budget for every currency decision.
ALLOCATION_SNAPSHOT_SCHEMA: Final = 3

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
    #: Strategy -> asset -> its own signed position, or ``None`` when the run's
    #: positions were never recorded (schema 2; see
    #: :attr:`~alphalab.allocation.state.AllocationState.strategy_positions`).
    strategy_positions: Mapping[str, Mapping[str, Decimal]] | None = None
    #: Each ceilinged strategy's committed capital (schema 3; see
    #: :attr:`~alphalab.allocation.state.AllocationState.strategy_capital`).
    strategy_capital: Mapping[str, StrategyCapital] = field(default_factory=dict)
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
        strategy_positions=None
        if state.strategy_positions is None
        else {
            strategy_id: dict(assets) for strategy_id, assets in state.strategy_positions.items()
        },
        strategy_capital=dict(state.strategy_capital),
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
        strategy_positions=None
        if snapshot.strategy_positions is None
        else PersistentMap(
            {
                strategy_id: PersistentMap(assets)
                for strategy_id, assets in snapshot.strategy_positions.items()
            }
        ),
        strategy_capital=PersistentMap(snapshot.strategy_capital),
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
        currency=as_str(require(payload, "currency"), "budget.currency"),
        enforce_strategy_budgets=as_bool(
            require(payload, "enforce_strategy_budgets"), "budget.enforce_strategy_budgets"
        ),
    )


def _strategy_capital(value: Any) -> dict[str, StrategyCapital]:
    where = "strategy_capital"
    ledger: dict[str, StrategyCapital] = {}
    for strategy_id, record in as_mapping(value, where).items():
        here = f"{where}[{strategy_id!r}]"
        payload = as_mapping(record, here)
        ledger[str(strategy_id)] = StrategyCapital(
            deployed=PersistentMap(
                as_decimal_mapping(require(payload, "deployed"), f"{here}.deployed")
            ),
            reserved=PersistentMap(
                as_decimal_mapping(require(payload, "reserved"), f"{here}.reserved")
            ),
            deployed_total=as_decimal(require(payload, "deployed_total"), f"{here}.deployed_total"),
            reserved_total=as_decimal(require(payload, "reserved_total"), f"{here}.reserved_total"),
        )
    return ledger


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
        terms=as_order_terms(require(payload, "terms"), f"{where}.terms"),
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
    for event_field in fields(cls):
        raw = require(payload, event_field.name)
        decoder = _EVENT_FIELD_DECODERS.get(event_field.name)
        kwargs[event_field.name] = (
            decoder(raw, f"{where}.{event_field.name}")
            if decoder is not None
            else as_str(raw, f"{where}.{event_field.name}")
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


#: How every allocation payload a release has written is read by this one. See
#: :mod:`alphalab.persistence.upgrade`.
def _strategy_positions(value: Any) -> dict[str, dict[str, Decimal]] | None:
    if value is None:
        return None
    where = "strategy_positions"
    return {
        str(strategy_id): as_decimal_mapping(assets, f"{where}[{strategy_id!r}]")
        for strategy_id, assets in as_mapping(value, where).items()
    }


def _v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Give each allocated request its terms, and record that positions were not kept.

    Allocation could emit only a market order good for the day until v3.11
    (ledger EXE-003), so that is what each version-1 request was. A version-1
    allocation kept no per-strategy positions (ledger FEA-001), and the payload
    holds nothing they could be rebuilt from, so they are ``None`` -- not
    recorded -- rather than empty, which would say every strategy is flat.
    """

    history = [
        {**request, "terms": dict(MARKET_TERMS_PAYLOAD)} if isinstance(request, dict) else request
        for request in payload.get("history", ())
    ]
    return {**payload, "history": history, "strategy_positions": None}


def _v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    """Record that a version-2 budget enforced no ceiling, and that nothing was committed.

    Nothing before v3.12 enforced a strategy's budget (ledger OFE-003), so the
    flag is ``False`` and the ledger it keeps is empty -- what the payload
    meant, not a value invented for it. A budget written before v2.17 named no
    currency, which is ``""``: unstated, what such a budget was.
    """

    budget = {"currency": "", **payload["budget"], "enforce_strategy_budgets": False}
    return {**payload, "budget": budget, "strategy_capital": {}}


ALLOCATION_SCHEMA_HISTORY = SchemaHistory(
    _SUBSYSTEM,
    ALLOCATION_SNAPSHOT_SCHEMA,
    (
        SchemaStep(
            1,
            "version 2 records each allocated request's order terms and each strategy's own "
            "positions; every version-1 request was a market order good for the day, and its "
            "positions were not kept",
            upgrade=_v1_to_v2,
        ),
        SchemaStep(
            2,
            "version 3 records whether the budget enforces per-strategy ceilings and what each "
            "ceilinged strategy has committed; no earlier budget enforced one",
            upgrade=_v2_to_v3,
        ),
    ),
)


def from_primitives(payload: Mapping[str, Any]) -> AllocationSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`AllocationSnapshot`.

    Raises:
        StateDecodeError: If the payload is not an object, declares no schema
            version or one this build does not read, is missing a field, or holds
            a value of the wrong type. The message names the field.
    """

    payload = as_mapping(payload, "allocation snapshot")
    payload = ALLOCATION_SCHEMA_HISTORY.upgrade(payload)

    return AllocationSnapshot(
        budget=_budget(require(payload, "budget")),
        history=tuple(
            _order_request(item, index) for index, item in enumerate(_indexed(payload, "history"))
        ),
        events=tuple(_event(item, index) for index, item in enumerate(_indexed(payload, "events"))),
        notional_allocated=as_decimal(require(payload, "notional_allocated"), "notional_allocated"),
        reservations=as_decimal_mapping(require(payload, "reservations"), "reservations"),
        contributions=_ledger(payload),
        strategy_positions=_strategy_positions(require(payload, "strategy_positions")),
        strategy_capital=_strategy_capital(require(payload, "strategy_capital")),
        schema_version=ALLOCATION_SNAPSHOT_SCHEMA,
    )
