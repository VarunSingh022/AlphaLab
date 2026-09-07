"""The allocation ledgers survive a round trip, and still behave afterwards.

``AllocationState`` owns the two ledgers that decide whether a run's committed
capital and its attribution are correct: ``reservations``, the per-order record
of capital still held, and ``contributions``, the record of which strategies
asked for each order. ADR-0021 made their lifetime exact and ADR-0024 gave a
restored working order a way to end -- and neither guarantee survives a round
trip the ledgers do not. That is why allocation gets a versioned snapshot of its
own rather than riding inline in a larger envelope (ADR-0023).

The state used here is deliberately not a set of empty collections. It carries
three reservations against three orders, one of them netted from two strategies
so a contribution tuple has more than one entry, one partially filled through the
venue path so its exposure is a residual rather than a whole, ten events across
four event types, and a non-zero ``notional_allocated``.

There is no legacy compatibility path and that is a decision, not an omission:
allocation has never had a ``capture``, a ``restore`` or a ``from_primitives``,
so although ``serialize`` would encode the state incidentally -- as it does for
market, risk and execution state -- nothing could ever read one back. A format
with no reader has no compatibility surface, which is why this differs from
:mod:`alphalab.oms.snapshot`, whose JSON round trip was a documented public
recipe.
"""

import uuid
from dataclasses import fields, replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.sizing import FixedQuantitySizing
from alphalab.allocation.snapshot import (
    ALLOCATION_SNAPSHOT_SCHEMA,
    AllocationEventRecord,
    AllocationSnapshot,
    capture,
    from_primitives,
    restore,
)
from alphalab.allocation.state import AllocationState
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.ids import current_id_position, id_scope
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import Side
from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.oms.order import Order as OMSOrder
from alphalab.oms.status import OrderStatus
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.strategy.events import Intent
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

SEED = 909
ASSET_ID = str(uuid.uuid4())
STRATEGY_ID = str(uuid.uuid4())
OTHER_STRATEGY_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Realistic populated state
# ---------------------------------------------------------------------------


def _netted_state() -> AllocationState:
    """One order netted from two strategies, so a contribution tuple has two entries."""

    state = AllocationEngine.initialize(
        CapitalBudget(
            global_capital=Decimal("1000000"),
            maximum_exposure=Decimal("2000000"),
            cash_buffer=Decimal("500"),
            strategy_budgets={STRATEGY_ID: Decimal("400000"), OTHER_STRATEGY_ID: Decimal("300000")},
        )
    )
    intents = (
        Intent(strategy_id=STRATEGY_ID, instrument=ASSET_ID, target=Decimal("60"), timestamp=2.0),
        Intent(
            strategy_id=OTHER_STRATEGY_ID, instrument=ASSET_ID, target=Decimal("40"), timestamp=2.0
        ),
    )
    state, _ = AllocationEngine.allocate(
        state,
        intents,
        {ASSET_ID: Decimal("100.00")},
        FixedQuantitySizing(),
        AllocationConstraints(allow_shorting=True, enforce_integer_quantities=False),
        2.0,
    )
    return state


def _lifecycle_state() -> tuple[ExecutionPipelineState, tuple[OMSOrder, ...]]:
    """Three working external orders, one of them partially filled by a venue."""

    config = replace(pipeline_config(STRATEGY_ID), routing=ExecutionRouting.EXTERNAL)
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            STRATEGY_ID,
            ScriptedStrategy(
                STRATEGY_ID,
                ASSET_ID,
                {2.0: Decimal("10"), 3.0: Decimal("6"), 4.0: Decimal("4")},
            ),
        ),
        1.0,
    )
    for timestamp in (2.0, 3.0, 4.0):
        state = ExecutionPipeline.process_quote(
            state, sized_quote(ASSET_ID, timestamp, Decimal("100"), Decimal("100")), context_factory
        ).state

    orders = tuple(state.oms.orders.open_orders())
    partial = ExecutionReport(
        execution_id=str(uuid.uuid4()),
        order_id=str(orders[1].order_id.value),
        asset_id=ASSET_ID,
        strategy_id="",
        timestamp=5.0,
        fill_price=Decimal("100"),
        fill_quantity=Decimal("2"),
        commission=Decimal("0"),
        slippage=Decimal("0"),
        liquidity_flag="",
        venue="LIVE",
        currency="USD",
        status=FillStatus.PARTIAL_FILL,
    )
    state, _, _ = ExecutionPipeline.apply_execution_report(state, orders[1], partial)
    return state, orders


def _payload(state: AllocationState) -> dict[str, Any]:
    return dict(deserialize(serialize(capture(state))))


def _round_trip(state: AllocationState) -> AllocationState:
    return restore(from_primitives(_payload(state)))


def test_the_state_under_test_is_realistically_populated() -> None:
    """Guards the tests below from silently becoming assertions about emptiness."""

    with id_scope(SEED):
        pipeline, _ = _lifecycle_state()
    state = pipeline.allocation

    assert len(state.reservations) == 3
    assert len(state.contributions) == 3
    assert state.notional_allocated > Decimal("0")
    assert len(state.history) == 3
    assert len(state.events) == 10
    assert len({type(event).__name__ for event in state.events}) == 4

    netted = _netted_state()
    assert any(len(entries) == 2 for entries in netted.contributions.values())


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_the_schema_constant_is_one() -> None:
    assert ALLOCATION_SNAPSHOT_SCHEMA == 1


def test_the_constant_is_not_an_alias_of_the_shared_default() -> None:
    """It must be independently settable, or the next bump versions every event."""

    import inspect

    from alphalab.allocation import snapshot as allocation_snapshot

    source = inspect.getsource(allocation_snapshot)

    assert not hasattr(allocation_snapshot, "DEFAULT_SCHEMA_VERSION")
    assert "ALLOCATION_SNAPSHOT_SCHEMA: Final = 1" in source
    assert "= DEFAULT_SCHEMA_VERSION" not in source


def test_capture_declares_the_version() -> None:
    state = _netted_state()

    assert capture(state).schema_version == ALLOCATION_SNAPSHOT_SCHEMA
    assert _payload(state)["schema_version"] == 1


def test_a_missing_version_is_refused_with_no_legacy_path() -> None:
    """Nothing could ever read an allocation payload, so there is nothing to support."""

    payload = _payload(_netted_state())
    del payload["schema_version"]

    with pytest.raises(StateDecodeError, match="missing 'schema_version'"):
        from_primitives(payload)


@pytest.mark.parametrize("version", [2, 3, 99, 0, -1])
def test_an_unreadable_version_is_refused_naming_it(version: int) -> None:
    payload = _payload(_netted_state())
    payload["schema_version"] = version

    with pytest.raises(StateDecodeError, match=f"declares schema version {version}"):
        from_primitives(payload)


@pytest.mark.parametrize("version", ["1", 1.0, True, None, []])
def test_a_malformed_version_is_refused(version: object) -> None:
    payload = _payload(_netted_state())
    payload["schema_version"] = version

    with pytest.raises(StateDecodeError, match="schema_version is not an integer"):
        from_primitives(payload)


def test_the_refusal_names_the_allocation_subsystem() -> None:
    payload = _payload(_netted_state())
    payload["schema_version"] = 2

    with pytest.raises(StateDecodeError) as excinfo:
        from_primitives(payload)

    assert str(excinfo.value).startswith("allocation snapshot")


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_an_initial_state_round_trips() -> None:
    state = AllocationEngine.initialize(CapitalBudget(Decimal("100"), Decimal("200")))

    assert restore(capture(state)) == state
    assert _round_trip(state) == state


def test_a_netted_state_round_trips_in_memory_and_across_json() -> None:
    state = _netted_state()

    assert restore(capture(state)) == state
    assert _round_trip(state) == state


def test_a_full_lifecycle_state_round_trips() -> None:
    with id_scope(SEED):
        pipeline, _ = _lifecycle_state()

    assert _round_trip(pipeline.allocation) == pipeline.allocation


def test_every_durable_field_survives_field_by_field() -> None:
    with id_scope(SEED):
        pipeline, _ = _lifecycle_state()
    state = pipeline.allocation
    restored = _round_trip(state)

    assert restored.budget == state.budget
    assert restored.notional_allocated == state.notional_allocated
    assert dict(restored.reservations) == dict(state.reservations)
    assert dict(restored.contributions) == dict(state.contributions)
    assert restored.history.to_tuple() == state.history.to_tuple()
    assert list(restored.events) == list(state.events)


def test_the_event_log_keeps_its_order_and_its_types() -> None:
    with id_scope(SEED):
        pipeline, _ = _lifecycle_state()
    state = pipeline.allocation
    restored = _round_trip(state)

    assert [type(event).__name__ for event in restored.events] == [
        type(event).__name__ for event in state.events
    ]
    assert [event.event_id for event in restored.events] == [
        event.event_id for event in state.events
    ]
    assert [event.timestamp for event in restored.events] == [
        event.timestamp for event in state.events
    ]


def test_a_multi_strategy_contribution_tuple_survives_intact() -> None:
    state = _netted_state()
    restored = _round_trip(state)

    order_id, entries = next(
        (key, value) for key, value in state.contributions.items() if len(value) == 2
    )

    assert restored.contributions[order_id] == entries
    assert [entry.strategy_id for entry in restored.contributions[order_id]] == sorted(
        {STRATEGY_ID, OTHER_STRATEGY_ID}
    )
    assert sum(entry.quantity for entry in restored.contributions[order_id]) == Decimal("100")


def test_request_history_keeps_identifiers_sides_and_timestamps() -> None:
    state = _netted_state()
    restored = _round_trip(state)

    for original, decoded in zip(state.history, restored.history, strict=True):
        assert decoded.order_id == original.order_id
        assert decoded.strategy_id == original.strategy_id
        assert decoded.asset_id == original.asset_id
        assert decoded.side is original.side
        assert isinstance(decoded.side, Side)
        assert decoded.quantity == original.quantity
        assert decoded.price == original.price
        assert decoded.timestamp == original.timestamp
        assert decoded.contributions == original.contributions


def test_the_budget_survives_including_its_strategy_map() -> None:
    state = _netted_state()
    restored = _round_trip(state)

    assert restored.budget.global_capital == Decimal("1000000")
    assert restored.budget.maximum_exposure == Decimal("2000000")
    assert restored.budget.cash_buffer == Decimal("500")
    assert dict(restored.budget.strategy_budgets) == {
        STRATEGY_ID: Decimal("400000"),
        OTHER_STRATEGY_ID: Decimal("300000"),
    }
    assert restored.budget.available_global_capital == Decimal("999500")


# ---------------------------------------------------------------------------
# Containers and determinism
# ---------------------------------------------------------------------------


def test_restore_rebuilds_the_native_persistent_containers() -> None:
    restored = _round_trip(_netted_state())

    assert isinstance(restored.reservations, PersistentMap)
    assert isinstance(restored.contributions, PersistentMap)
    assert isinstance(restored.history, AppendOnlyLog)
    assert isinstance(restored.events, AppendOnlyLog)


def test_the_rebuilt_containers_still_behave_as_ledgers() -> None:
    """Equality is not enough: the restored maps must still be usable ledgers."""

    state = _netted_state()
    restored = _round_trip(state)
    order_id = next(iter(state.reservations))

    assert order_id in restored.reservations
    assert restored.reservations[order_id] == state.reservations[order_id]
    assert AllocationEngine.reserved_notional(restored, order_id) == state.reservations[order_id]
    assert AllocationEngine.contributions_for(restored, order_id) == state.contributions[order_id]

    retired = AllocationEngine.retire_contributions(restored, order_id)
    assert order_id not in retired.contributions
    assert order_id in restored.contributions, "the original view is unchanged"


def test_serialization_is_deterministic_and_stable_across_a_re_capture() -> None:
    state = _netted_state()
    payload = serialize(capture(state))

    assert payload == serialize(capture(state))
    assert serialize(capture(_round_trip(state))) == payload


def test_the_payload_carries_exactly_the_projected_fields() -> None:
    assert set(_payload(_netted_state())) == {
        "budget",
        "history",
        "events",
        "notional_allocated",
        "reservations",
        "contributions",
        "schema_version",
    }


# ---------------------------------------------------------------------------
# Real lifecycle state, and the lifecycle still working after restore
# ---------------------------------------------------------------------------


def test_a_working_orders_reservation_survives_the_round_trip() -> None:
    with id_scope(SEED):
        pipeline, orders = _lifecycle_state()
    restored = _round_trip(pipeline.allocation)

    for order in orders:
        key = str(order.order_id.value)
        assert key in restored.reservations
        assert key in restored.contributions


def test_a_partially_filled_orders_residual_exposure_survives() -> None:
    """The second order was filled 2 of 6 through the venue path."""

    with id_scope(SEED):
        pipeline, orders = _lifecycle_state()
    key = str(orders[1].order_id.value)
    original = pipeline.allocation.reservations[key]
    restored = _round_trip(pipeline.allocation)

    assert original < Decimal("600"), "a partial fill consumed part of the reservation"
    assert restored.reservations[key] == original


def test_terminalizing_a_restored_working_order_retires_exactly_that_order() -> None:
    """Step 4's bridge, driven against a restored allocation ledger."""

    with id_scope(SEED):
        pipeline, orders = _lifecycle_state()

    restored_pipeline = replace(pipeline, allocation=_round_trip(pipeline.allocation))
    target = orders[0]
    key = str(target.order_id.value)
    untouched = {other for other in restored_pipeline.allocation.reservations if other != key}

    assert len(restored_pipeline.allocation.reservations) == 3

    terminated = ExecutionPipeline.apply_terminal_outcome(
        restored_pipeline, target, OrderStatus.CANCELLED, 9.0
    )

    assert terminated.oms.orders.find(target.order_id).status is OrderStatus.CANCELLED
    assert key not in terminated.allocation.reservations
    assert key not in terminated.allocation.contributions
    assert set(terminated.allocation.reservations) == untouched
    assert set(terminated.allocation.contributions) == untouched


def test_a_refused_repeat_after_restore_does_not_double_retire() -> None:
    from alphalab.oms.exceptions import InvalidTransitionError

    with id_scope(SEED):
        pipeline, orders = _lifecycle_state()

    restored_pipeline = replace(pipeline, allocation=_round_trip(pipeline.allocation))
    terminated = ExecutionPipeline.apply_terminal_outcome(
        restored_pipeline, orders[0], OrderStatus.CANCELLED, 9.0
    )
    after = dict(terminated.allocation.reservations)

    with pytest.raises(InvalidTransitionError):
        ExecutionPipeline.apply_terminal_outcome(
            terminated,
            terminated.oms.orders.find(orders[0].order_id),
            OrderStatus.CANCELLED,
            10.0,
        )

    assert dict(terminated.allocation.reservations) == after
    assert len(after) == 2


def test_the_notional_total_stays_consistent_with_the_ledger_after_restore() -> None:
    with id_scope(SEED):
        pipeline, orders = _lifecycle_state()

    restored_pipeline = replace(pipeline, allocation=_round_trip(pipeline.allocation))
    released = restored_pipeline.allocation.reservations[str(orders[0].order_id.value)]
    before = restored_pipeline.allocation.notional_allocated

    terminated = ExecutionPipeline.apply_terminal_outcome(
        restored_pipeline, orders[0], OrderStatus.CANCELLED, 9.0
    )

    assert terminated.allocation.notional_allocated == before - released


# ---------------------------------------------------------------------------
# Identifiers and the deterministic stream
# ---------------------------------------------------------------------------


def test_restore_mints_no_identifier() -> None:
    state = _netted_state()
    payload = _payload(state)

    restored = restore(from_primitives(payload))

    assert [request.order_id for request in restored.history] == [
        request.order_id for request in state.history
    ]
    assert [event.event_id for event in restored.events] == [
        event.event_id for event in state.events
    ]
    assert sorted(restored.reservations) == sorted(state.reservations)
    assert sorted(restored.contributions) == sorted(state.contributions)


def test_decoding_does_not_advance_the_deterministic_id_stream() -> None:
    """Step 5 owns the stream; snapshot decoding must stay clear of it."""

    payload = _payload(_netted_state())

    with id_scope(SEED):
        before = current_id_position()
        restore(from_primitives(payload))
        after = current_id_position()

    assert before == after
    assert after.draws == 0


def test_the_module_does_not_touch_the_id_source() -> None:
    import ast
    import inspect

    from alphalab.allocation import snapshot as allocation_snapshot

    tree = ast.parse(inspect.getsource(allocation_snapshot))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported
    assert "alphalab.common.ids" not in imported
    assert not hasattr(allocation_snapshot, "id_source_for")
    assert not hasattr(allocation_snapshot, "new_id")


# ---------------------------------------------------------------------------
# Corruption and refusal
# ---------------------------------------------------------------------------


def test_a_non_object_payload_is_refused() -> None:
    with pytest.raises(StateDecodeError, match="allocation snapshot is not an object"):
        from_primitives([])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "key",
    ["budget", "history", "events", "notional_allocated", "reservations", "contributions"],
)
def test_a_missing_required_field_is_refused_naming_it(key: str) -> None:
    payload = _payload(_netted_state())
    del payload[key]

    with pytest.raises(StateDecodeError, match=f"missing '{key}'"):
        from_primitives(payload)


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("reservations", [1, 2], "reservations is not an object"),
        ("notional_allocated", None, "notional_allocated is not a decimal"),
        ("history", {"nope": 1}, "history is not an array"),
        ("events", {"nope": 1}, "events is not an array"),
        ("budget", "money", "budget is not an object"),
        ("contributions", [], "contributions is not an object"),
    ],
)
def test_a_wrongly_typed_field_is_refused_naming_it(key: str, value: Any, match: str) -> None:
    payload = _payload(_netted_state())
    payload[key] = value

    with pytest.raises(StateDecodeError, match=match):
        from_primitives(payload)


def test_an_unknown_event_type_is_refused_not_guessed() -> None:
    payload = _payload(_netted_state())
    payload["events"][0]["event_type"] = "AllocationTeleported"

    with pytest.raises(StateDecodeError, match="not an allocation event"):
        from_primitives(payload)


def test_a_missing_event_field_is_refused() -> None:
    payload = _payload(_netted_state())
    del payload["events"][0]["event"]["timestamp"]

    with pytest.raises(StateDecodeError, match="missing 'timestamp'"):
        from_primitives(payload)


def test_a_malformed_reservation_amount_is_refused() -> None:
    payload = _payload(_netted_state())
    key = next(iter(payload["reservations"]))
    payload["reservations"][key] = "not-a-number"

    with pytest.raises(StateDecodeError, match="reservations"):
        from_primitives(payload)


def test_a_malformed_contribution_is_refused() -> None:
    payload = _payload(_netted_state())
    key = next(iter(payload["contributions"]))
    payload["contributions"][key] = [{"strategy_id": "S"}]

    with pytest.raises(StateDecodeError, match="missing 'quantity'"):
        from_primitives(payload)


def test_a_malformed_request_side_is_refused() -> None:
    payload = _payload(_netted_state())
    payload["history"][0]["side"] = "sideways"

    with pytest.raises(StateDecodeError, match="side is not a Side"):
        from_primitives(payload)


# ---------------------------------------------------------------------------
# Field coverage
# ---------------------------------------------------------------------------


def test_the_snapshot_covers_every_allocation_state_field() -> None:
    """A new durable field must be projected, or declared here as not carried."""

    carried = {field.name for field in fields(AllocationSnapshot)}
    missing = {field.name for field in fields(AllocationState)} - carried

    assert not missing, (
        f"AllocationState fields absent from AllocationSnapshot: {sorted(missing)}. "
        "Add them to capture/restore/from_primitives, or state here why they are "
        "deliberately not carried."
    )


def test_the_snapshot_carries_nothing_the_state_does_not_have() -> None:
    derived = {"schema_version"}
    unexpected = (
        {field.name for field in fields(AllocationSnapshot)}
        - {field.name for field in fields(AllocationState)}
        - derived
    )

    assert not unexpected, f"AllocationSnapshot invents: {sorted(unexpected)}"


def test_the_event_record_tags_every_allocation_event_type() -> None:
    """A new event type must be registered, or its payload cannot be read back."""

    from alphalab.allocation import events as allocation_events
    from alphalab.allocation.snapshot import _EVENT_TYPES

    declared = {
        name
        for name, value in vars(allocation_events).items()
        if isinstance(value, type)
        and issubclass(value, allocation_events.AllocationEvent)
        and value is not allocation_events.AllocationEvent
    }

    assert declared == set(_EVENT_TYPES), (
        f"allocation event types not tagged by the snapshot: {sorted(declared - set(_EVENT_TYPES))}"
    )
    assert {field.name for field in fields(AllocationEventRecord)} == {"event_type", "event"}


def test_no_pipeline_or_session_snapshot_exists_yet() -> None:
    """Step 6 is allocation only; the envelope is later work."""

    import alphalab.runtime.execution_pipeline as pipeline_module
    import alphalab.runtime.session as session_module

    assert not hasattr(pipeline_module, "PipelineSnapshot")
    assert not hasattr(pipeline_module, "PIPELINE_SNAPSHOT_SCHEMA")
    assert not hasattr(pipeline_module, "capture")
    assert not hasattr(session_module, "RunSnapshot")
    assert not hasattr(session_module, "SESSION_SNAPSHOT_SCHEMA")
