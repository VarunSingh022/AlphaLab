"""The composite execution-path state round-trips, and refuses what it cannot rebuild.

``ExecutionPipelineState`` is the state every environment threads -- a backtest, a
replay, a paper run and a live session all advance the same value through the
same step -- and until v2.9 nothing captured it. Measured on v2.8.0, eleven of
its fifteen fields already serialized as they stood; the obstacle was never the
state model but that four values are not data, and no contract said what to do
about them.

ADR-0014 had already answered that for the lifecycle: record what the object
*was*, and require the caller to supply it back, raising rather than substituting.
This envelope applies that rule to the execution path, reuses the portfolio, OMS
and allocation snapshots rather than decoding their contents a second time, and
carries market, risk, execution, analytics and the strategy runtime inline under
its own version.

Two properties are load-bearing and easy to lose:

* ``capture`` takes ``id_position`` from the **state**, never from the ambient
  identifier source. ``test_capture_reads_the_position_from_the_state`` pins it
  by capturing while a *different* source is installed.
* ``restore`` re-runs ``_require_one_account_currency``. That validator is called
  by ``initialize`` and by nothing else, so a restore that skipped it could
  rebuild a state ``initialize`` would have refused -- an invariant that stops
  being checked on the second path into the same state.

See ADR-0023.
"""

import uuid
from dataclasses import fields, replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation.sizing import EqualWeightSizing
from alphalab.allocation.snapshot import ALLOCATION_SNAPSHOT_SCHEMA
from alphalab.common.ids import (
    DeterministicIdSource,
    IdStreamPosition,
    current_id_position,
    id_scope,
    new_id,
    use_id_source,
)
from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.instrument.registry import InstrumentRegistry
from alphalab.oms.snapshot import OMS_SNAPSHOT_SCHEMA
from alphalab.oms.snapshot import SnapshotDecodeError as OMSSnapshotDecodeError
from alphalab.oms.status import OrderStatus
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.portfolio.account import Account
from alphalab.portfolio.snapshot import PORTFOLIO_SNAPSHOT_SCHEMA
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.runtime.snapshot import (
    PIPELINE_SNAPSHOT_SCHEMA,
    PipelineSnapshot,
    RuntimeObjects,
    capture,
    from_primitives,
    restore,
)
from alphalab.strategy.state import LifecycleState
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

SEED = 31337
ASSET_ID = str(uuid.uuid4())
UNPRICED_ID = str(uuid.uuid4())
STRATEGY_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# One realistically populated state, and the objects it needs back
# ---------------------------------------------------------------------------


def _populated() -> tuple[ExecutionPipelineState, RuntimeObjects, tuple[Any, ...]]:
    """A state exercising every durable field, not a set of empty collections.

    Three working external orders (so allocation holds three reservations and
    three contributions), one of them partially filled through the venue path
    (so the execution ledger, the fills, trades and trade records are populated
    and the reservation is a residual), one intent for an asset that was never
    priced (so ``unpriced_assets`` is populated), a configured instrument
    registry, and compiled analytics.
    """

    strategy = ScriptedStrategy(
        STRATEGY_ID,
        ASSET_ID,
        {2.0: Decimal("10"), 3.0: Decimal("6"), 4.0: Decimal("4"), 5.0: Decimal("3")},
        asset_for={5.0: UNPRICED_ID},
    )
    config = replace(
        pipeline_config(STRATEGY_ID),
        routing=ExecutionRouting.EXTERNAL,
        instruments=InstrumentRegistry(),
    )
    state = ExecutionPipeline.initialize(config, running_strategy_state(STRATEGY_ID, strategy), 1.0)
    for timestamp in (2.0, 3.0, 4.0, 5.0):
        state = ExecutionPipeline.process_quote(
            state, sized_quote(ASSET_ID, timestamp, Decimal("100"), Decimal("100")), context_factory
        ).state

    orders = tuple(state.oms.orders.open_orders())
    report = ExecutionReport(
        execution_id=str(uuid.uuid4()),
        order_id=str(orders[1].order_id.value),
        asset_id=ASSET_ID,
        strategy_id="",
        timestamp=6.0,
        fill_price=Decimal("101"),
        fill_quantity=Decimal("2"),
        commission=Decimal("0.5"),
        slippage=Decimal("0"),
        liquidity_flag="",
        venue="LIVE",
        currency="USD",
        status=FillStatus.PARTIAL_FILL,
    )
    state, _, _ = ExecutionPipeline.apply_execution_report(state, orders[1], report)
    state = ExecutionPipeline.compile_analytics(state, 7.0)

    objects = RuntimeObjects(
        sizing_model=config.sizing_model,
        simulator=config.simulator,
        strategies={STRATEGY_ID: strategy},
        instruments=config.instruments,
    )
    return state, objects, orders


def _state() -> tuple[ExecutionPipelineState, RuntimeObjects, tuple[Any, ...]]:
    with id_scope(SEED):
        return _populated()


def _payload(state: ExecutionPipelineState) -> dict[str, Any]:
    return dict(deserialize(serialize(capture(state))))


def _round_trip(state: ExecutionPipelineState, objects: RuntimeObjects) -> ExecutionPipelineState:
    return restore(from_primitives(_payload(state)), objects)


def test_the_state_under_test_exercises_every_durable_field() -> None:
    """Guards every test below from becoming an assertion about emptiness."""

    state, _, _ = _state()

    assert len(state.market.latest_quotes) >= 1
    assert len(state.market.events) == 4
    assert len(state.strategy.strategies) == 1
    assert len(state.allocation.reservations) == 3
    assert len(state.allocation.contributions) == 3
    assert len(state.allocation.events) > 10
    assert len(state.risk.history) == 3
    assert len(state.risk.events) > 10
    assert len(list(state.oms.orders.orders())) == 3
    assert len(state.oms.events) > 0
    assert len(state.execution.reports) == 1
    assert len(state.execution.events) == 1
    assert len(state.portfolio.positions) == 1
    assert len(state.portfolio.events) > 0
    assert len(state.analytics.reports) == 1
    assert len(state.analytics.events) == 1
    assert len(state.market_prices) == 1
    assert len(state.fills) == 1
    assert len(state.trades) == 1
    assert len(state.trade_records) == 1
    assert len(state.portfolio_snapshots) == 5
    assert len(state.unpriced_assets) == 1
    assert state.id_position.seed == SEED
    assert state.id_position.draws > 0
    assert state.config.instruments is not None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_the_schema_constant_is_two() -> None:
    """v2.10 moved it once, for the strategy-state field. See ADR-0025 decision 8."""

    assert PIPELINE_SNAPSHOT_SCHEMA == 2


def test_the_constant_is_not_an_alias_of_the_shared_default() -> None:
    import inspect

    from alphalab.runtime import snapshot as pipeline_snapshot

    source = inspect.getsource(pipeline_snapshot)

    assert not hasattr(pipeline_snapshot, "DEFAULT_SCHEMA_VERSION")
    assert "PIPELINE_SNAPSHOT_SCHEMA: Final = 2" in source
    assert "= DEFAULT_SCHEMA_VERSION" not in source


def test_capture_declares_the_version() -> None:
    state, _, _ = _state()

    assert capture(state).schema_version == PIPELINE_SNAPSHOT_SCHEMA
    assert _payload(state)["schema_version"] == 2


def test_a_missing_version_is_refused_with_no_legacy_path() -> None:
    """Pipeline snapshots are new in v2.9; there is nothing to be compatible with."""

    state, _, _ = _state()
    payload = _payload(state)
    del payload["schema_version"]

    with pytest.raises(StateDecodeError, match="missing 'schema_version'"):
        from_primitives(payload)


@pytest.mark.parametrize("version", [3, 99, 0, -1])
def test_an_unreadable_version_is_refused_naming_it(version: int) -> None:
    state, _, _ = _state()
    payload = _payload(state)
    payload["schema_version"] = version

    with pytest.raises(StateDecodeError, match=f"declares schema version {version}"):
        from_primitives(payload)


@pytest.mark.parametrize("version", ["1", 1.0, True, None, []])
def test_a_malformed_version_is_refused(version: object) -> None:
    state, _, _ = _state()
    payload = _payload(state)
    payload["schema_version"] = version

    with pytest.raises(StateDecodeError, match="schema_version is not an integer"):
        from_primitives(payload)


def test_the_refusal_names_the_pipeline_subsystem() -> None:
    state, _, _ = _state()
    payload = _payload(state)
    payload["schema_version"] = 3

    with pytest.raises(StateDecodeError) as excinfo:
        from_primitives(payload)

    assert str(excinfo.value).startswith("pipeline snapshot")


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_a_populated_state_round_trips_in_memory() -> None:
    state, objects, _ = _state()

    assert restore(capture(state), objects) == state


def test_a_populated_state_round_trips_across_json() -> None:
    state, objects, _ = _state()

    assert _round_trip(state, objects) == state


def test_serialization_is_deterministic_and_stable_across_a_re_capture() -> None:
    state, objects, _ = _state()
    payload = serialize(capture(state))

    assert payload == serialize(capture(state))
    assert capture(_round_trip(state, objects)) == capture(state)
    assert serialize(capture(_round_trip(state, objects))) == payload


def test_the_payload_carries_exactly_the_projected_fields() -> None:
    state, _, _ = _state()

    assert set(_payload(state)) == {field.name for field in fields(PipelineSnapshot)}


@pytest.mark.parametrize(
    "field_name",
    [
        "config",
        "market",
        "strategy",
        "allocation",
        "risk",
        "oms",
        "execution",
        "portfolio",
        "analytics",
        "market_prices",
        "fills",
        "trades",
        "trade_records",
        "portfolio_snapshots",
        "unpriced_assets",
        "id_position",
    ],
)
def test_each_durable_field_survives(field_name: str) -> None:
    state, objects, _ = _state()
    restored = _round_trip(state, objects)

    assert getattr(restored, field_name) == getattr(state, field_name)


def test_the_market_indexes_and_logs_survive_with_their_order() -> None:
    state, objects, _ = _state()
    restored = _round_trip(state, objects)

    assert dict(restored.market.latest_quotes) == dict(state.market.latest_quotes)
    assert [type(e).__name__ for e in restored.market.events] == [
        type(e).__name__ for e in state.market.events
    ]
    assert restored.market.history.to_tuple() == state.market.history.to_tuple()


def test_the_strategy_runtime_metadata_survives_without_its_instance() -> None:
    state, objects, _ = _state()
    restored = _round_trip(state, objects)
    original = state.strategy.strategies[STRATEGY_ID]
    decoded = restored.strategy.strategies[STRATEGY_ID]

    assert decoded.status is original.status is LifecycleState.RUNNING
    assert decoded.config == original.config
    assert decoded.subscriptions == original.subscriptions == frozenset({"quotes"})
    assert decoded.last_error == original.last_error
    assert decoded.instance is objects.strategies[STRATEGY_ID], "the caller's object, not a copy"


def test_no_live_object_appears_in_the_payload() -> None:
    """A type name is evidence of what an object was, never the object."""

    state, _, _ = _state()
    payload = _payload(state)

    assert payload["config"]["sizing_model_type"] == "FixedQuantitySizing"
    assert payload["config"]["simulator_type"] == "ExecutionSimulator"
    assert payload["config"]["instruments_type"] == "InstrumentRegistry"
    assert payload["strategy"][0]["instance_type"] == "ScriptedStrategy"
    assert "sizing_model" not in payload["config"]
    assert "simulator" not in payload["config"]
    assert "instruments" not in payload["config"]
    assert "instance" not in payload["strategy"][0]


def test_the_unpriced_observability_state_survives() -> None:
    state, objects, _ = _state()
    restored = _round_trip(state, objects)
    original = state.unpriced_assets[UNPRICED_ID]
    decoded = restored.unpriced_assets[UNPRICED_ID]

    assert decoded == original
    assert decoded.reason is original.reason
    assert decoded.occurrences == original.occurrences
    assert decoded.detail == original.detail


# ---------------------------------------------------------------------------
# Nested snapshots keep their own versions and their own decoders
# ---------------------------------------------------------------------------


def test_the_nested_snapshots_declare_their_own_versions() -> None:
    state, _, _ = _state()
    payload = _payload(state)

    assert payload["allocation"]["schema_version"] == ALLOCATION_SNAPSHOT_SCHEMA
    assert payload["oms"]["schema_version"] == OMS_SNAPSHOT_SCHEMA
    assert payload["portfolio"]["schema_version"] == PORTFOLIO_SNAPSHOT_SCHEMA


@pytest.mark.parametrize(
    ("subsystem", "error", "match"),
    [
        ("allocation", StateDecodeError, "allocation snapshot declares schema version 2"),
        # The OMS decoder raises its own error type, which every OMS caller
        # catches; v2.9 deliberately kept that rather than flattening a nested
        # failure into an opaque pipeline one.
        ("oms", OMSSnapshotDecodeError, "oms snapshot declares schema version 2"),
        ("portfolio", StateDecodeError, "portfolio snapshot declares schema version 3"),
    ],
)
def test_a_nested_snapshot_is_validated_by_its_own_decoder(
    subsystem: str, error: type[Exception], match: str
) -> None:
    """Their errors reach the caller as their own, not flattened into a pipeline one."""

    state, _, _ = _state()
    payload = _payload(state)
    payload[subsystem]["schema_version"] = payload[subsystem]["schema_version"] + 1

    with pytest.raises(error, match=match):
        from_primitives(payload)


def test_every_nested_oms_payload_this_build_writes_carries_its_version() -> None:
    """So nothing inside the envelope relies on the Step 2 shape inference.

    Stripping the version from a nested payload *would* leave exactly the
    pre-v2.9 five-key shape, and the OMS decoder would then read it as
    ``LEGACY_UNVERSIONED_V0`` -- correctly, because that is what the shape rule
    says. What matters is that no payload this build produces ever reaches that
    branch: ``capture`` always declares the version.
    """

    from alphalab.oms.snapshot import LEGACY_UNVERSIONED_V0_KEYS

    state, _, _ = _state()
    nested = _payload(state)["oms"]

    assert "schema_version" in nested
    assert nested["schema_version"] == OMS_SNAPSHOT_SCHEMA
    assert set(nested) == LEGACY_UNVERSIONED_V0_KEYS | {"schema_version"}


def test_the_allocation_ledgers_survive_through_the_envelope() -> None:
    state, objects, _ = _state()
    restored = _round_trip(state, objects)

    assert dict(restored.allocation.reservations) == dict(state.allocation.reservations)
    assert dict(restored.allocation.contributions) == dict(state.allocation.contributions)
    assert restored.allocation.notional_allocated == state.allocation.notional_allocated
    assert list(restored.allocation.events) == list(state.allocation.events)


def test_the_oms_book_survives_through_the_envelope() -> None:
    state, objects, orders = _state()
    restored = _round_trip(state, objects)

    assert [o.order_id for o in restored.oms.orders.orders()] == [
        o.order_id for o in state.oms.orders.orders()
    ]
    assert set(restored.oms.active_orders) == set(state.oms.active_orders)
    assert restored.oms.orders.find(orders[1].order_id).filled_quantity == Decimal("2")


def test_the_portfolio_survives_through_the_envelope() -> None:
    state, objects, _ = _state()
    restored = _round_trip(state, objects)

    assert restored.portfolio.cash.balance("USD") == state.portfolio.cash.balance("USD")
    assert restored.portfolio.positions[ASSET_ID].quantity == Decimal("2")
    assert restored.portfolio.realized_pnl == state.portfolio.realized_pnl
    assert restored.portfolio.commission_paid == state.portfolio.commission_paid


# ---------------------------------------------------------------------------
# The runtime-object contract
# ---------------------------------------------------------------------------


def _objects(
    state: ExecutionPipelineState, base: RuntimeObjects, **overrides: Any
) -> RuntimeObjects:
    return replace(base, **overrides)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"sizing_model": None}, "No sizing model supplied"),
        ({"sizing_model": EqualWeightSizing(3)}, "sizing model supplied is a EqualWeightSizing"),
        ({"simulator": None}, "No simulator supplied"),
        ({"simulator": EqualWeightSizing(3)}, "simulator supplied is a EqualWeightSizing"),
        ({"strategies": {}}, "No strategy instance for"),
        ({"instruments": None}, "No instrument registry supplied"),
        ({"instruments": EqualWeightSizing(3)}, "instrument registry supplied is a"),
    ],
)
def test_a_missing_or_wrong_runtime_object_is_refused(
    overrides: dict[str, Any], match: str
) -> None:
    state, objects, _ = _state()
    snapshot = from_primitives(_payload(state))

    with pytest.raises(StateDecodeError, match=match):
        restore(snapshot, _objects(state, objects, **overrides))


def test_a_wrong_strategy_instance_is_refused_naming_the_strategy() -> None:
    state, objects, _ = _state()
    snapshot = from_primitives(_payload(state))

    with pytest.raises(StateDecodeError, match="strategy instance for"):
        restore(
            snapshot,
            replace(objects, strategies={STRATEGY_ID: EqualWeightSizing(3)}),  # type: ignore[dict-item]
        )


def test_supplying_a_registry_a_run_did_not_have_is_refused() -> None:
    """A registry changes what a run can say about an unpriced asset."""

    strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, {})
    config = pipeline_config(STRATEGY_ID)
    assert config.instruments is None
    state = ExecutionPipeline.initialize(config, running_strategy_state(STRATEGY_ID, strategy), 1.0)
    snapshot = from_primitives(_payload(state))
    objects = RuntimeObjects(config.sizing_model, config.simulator, {STRATEGY_ID: strategy})

    assert restore(snapshot, objects) == state

    with pytest.raises(StateDecodeError, match="records that the run configured none"):
        restore(snapshot, replace(objects, instruments=InstrumentRegistry()))


def test_a_recorded_type_name_is_never_imported_or_constructed() -> None:
    """A type string is evidence, not an instruction."""

    import ast
    import inspect

    from alphalab.runtime import snapshot as pipeline_snapshot

    source = inspect.getsource(pipeline_snapshot)
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "__import__" not in called
    assert "eval" not in called
    assert "getattr" not in called
    assert "import_module" not in source
    assert "importlib" not in source


# ---------------------------------------------------------------------------
# The identifier position
# ---------------------------------------------------------------------------


def test_capture_reads_the_position_from_the_state() -> None:
    """Not from the ambient source -- the reason the field lives on the state.

    A different source is installed and advanced while the capture runs; the
    snapshot must still record what the state says.
    """

    state, _, _ = _state()
    forced = replace(state, id_position=IdStreamPosition(seed=777, draws=12345))

    with use_id_source(DeterministicIdSource(999)):
        for _ in range(50):
            new_id()
        assert current_id_position() == IdStreamPosition(999, 50)
        recorded = capture(forced).id_position

    assert recorded == IdStreamPosition(777, 12345)


def test_a_non_zero_position_round_trips() -> None:
    state, objects, _ = _state()
    restored = _round_trip(state, objects)

    assert state.id_position.seed == SEED
    assert state.id_position.draws > 0
    assert restored.id_position == state.id_position


def test_an_unseeded_position_round_trips_as_unseeded() -> None:
    state, objects, _ = _populated()

    assert state.id_position == IdStreamPosition(None, 0)
    assert _round_trip(state, objects).id_position == IdStreamPosition(None, 0)


def test_restore_mints_no_identifier_and_does_not_advance_the_stream() -> None:
    state, objects, _ = _state()
    snapshot = from_primitives(_payload(state))

    with id_scope(SEED):
        before = current_id_position()
        restored = restore(snapshot, objects)
        after = current_id_position()

    assert before == after == IdStreamPosition(SEED, 0)

    def identifiers(value: ExecutionPipelineState) -> list[str]:
        return [
            *(str(order.order_id.value) for order in value.oms.orders.orders()),
            *(str(fill.fill_id) for fill in value.fills),
            *(str(trade.trade_id) for trade in value.trades),
            *(report.execution_id for report in value.execution.history),
            *(txn.transaction_id for txn in value.portfolio.ledger.transactions),
            *(event.event_id for event in value.allocation.events),
            *(decision.decision_id for decision in value.risk.history),
        ]

    assert identifiers(restored) == identifiers(state)
    assert len(set(identifiers(restored))) == len(identifiers(restored))


def test_the_module_never_installs_an_identifier_source() -> None:
    """Reconstruction and continuation are separate; Step 8 owns the second.

    Asserted over what the module imports and calls rather than over its text,
    because the docstrings name ``id_source_for`` deliberately -- saying which
    concern is *not* here is the point. What must not exist is the call.
    """

    import ast
    import inspect

    from alphalab.runtime import snapshot as pipeline_snapshot

    tree = ast.parse(inspect.getsource(pipeline_snapshot))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)

    for name in ("id_source_for", "use_id_source", "id_scope", "new_id"):
        assert name not in imported
        assert not hasattr(pipeline_snapshot, name)


def test_a_restored_position_can_later_drive_a_matching_stream() -> None:
    """State reconstruction is enough; continuation is the caller's next step."""

    from alphalab.common.ids import id_source_for

    state, objects, _ = _state()
    restored = _round_trip(state, objects)

    expected = id_source_for(state.id_position)
    actual = id_source_for(restored.id_position)
    assert expected is not None and actual is not None

    assert [actual() for _ in range(5)] == [expected() for _ in range(5)]


# ---------------------------------------------------------------------------
# Construction-time validation is replayed
# ---------------------------------------------------------------------------


def test_restore_refuses_what_initialize_would_have_refused() -> None:
    """``_require_one_account_currency`` is called by initialize and by nothing else."""

    state, objects, _ = _state()
    strategy_state = running_strategy_state(
        STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET_ID, {})
    )
    disagreeing = replace(state.config, account=Account("acct", "EUR", "n", 1.0))

    with pytest.raises(RuntimeValidationError) as from_initialize:
        ExecutionPipeline.initialize(disagreeing, strategy_state, 1.0)

    payload = _payload(state)
    payload["config"]["account"]["base_currency"] = "EUR"

    with pytest.raises(RuntimeValidationError) as from_restore:
        restore(from_primitives(payload), objects)

    assert str(from_restore.value) == str(from_initialize.value)


def test_the_currency_check_is_reused_rather_than_reimplemented() -> None:
    import inspect

    from alphalab.runtime import snapshot as pipeline_snapshot

    source = inspect.getsource(pipeline_snapshot)

    assert "_require_one_account_currency(config)" in source
    assert "base_currency" not in source.split('"""')[-1], "no second implementation of the rule"


# ---------------------------------------------------------------------------
# The external working order, end to end
# ---------------------------------------------------------------------------


def test_a_working_external_order_survives_and_then_terminalizes() -> None:
    state, objects, orders = _state()
    restored = _round_trip(state, objects)
    target = orders[0]
    key = str(target.order_id.value)
    untouched = {other for other in restored.allocation.reservations if other != key}

    current = restored.oms.orders.find(target.order_id)
    assert current.is_open
    assert current.status is OrderStatus.ACCEPTED
    assert key in restored.allocation.reservations
    assert key in restored.allocation.contributions

    terminated = ExecutionPipeline.apply_terminal_outcome(
        restored, current, OrderStatus.CANCELLED, 9.0
    )

    assert terminated.oms.orders.find(target.order_id).status is OrderStatus.CANCELLED
    assert key not in terminated.allocation.reservations
    assert key not in terminated.allocation.contributions
    assert set(terminated.allocation.reservations) == untouched


def test_a_restored_partially_filled_order_keeps_its_fill() -> None:
    state, objects, orders = _state()
    restored = _round_trip(state, objects)
    order = restored.oms.orders.find(orders[1].order_id)

    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("2")
    assert order.average_fill_price == Decimal("101")
    assert restored.portfolio.positions[ASSET_ID].quantity == Decimal("2")


def test_a_restored_duplicate_execution_is_still_a_no_op() -> None:
    """The Step 3 ledger survives, so redelivery is still recognised after restore."""

    state, objects, orders = _state()
    restored = _round_trip(state, objects)
    execution_id = next(iter(state.execution.reports))
    report = state.execution.reports[execution_id]

    again, fills, trades = ExecutionPipeline.apply_execution_report(
        restored, restored.oms.orders.find(orders[1].order_id), report
    )

    assert fills == ()
    assert trades == ()
    assert again is restored


# ---------------------------------------------------------------------------
# Independence and absence of side effects
# ---------------------------------------------------------------------------


def test_the_restored_state_is_independent_of_the_original() -> None:
    state, objects, orders = _state()
    restored = _round_trip(state, objects)

    advanced = ExecutionPipeline.apply_terminal_outcome(
        restored, restored.oms.orders.find(orders[0].order_id), OrderStatus.CANCELLED, 9.0
    )

    assert advanced is not state
    assert len(state.allocation.reservations) == 3, "the original is untouched"
    assert state.oms.orders.find(orders[0].order_id).is_open
    assert len(advanced.allocation.reservations) == 2


def test_capture_does_not_mutate_the_state() -> None:
    state, _, _ = _state()
    before = serialize(capture(state))

    capture(state)
    capture(state)

    assert serialize(capture(state)) == before


def test_restore_executes_nothing() -> None:
    """No order fills, no event is processed, no report is applied."""

    state, objects, _ = _state()
    restored = _round_trip(state, objects)

    assert len(restored.fills) == len(state.fills)
    assert len(restored.trades) == len(state.trades)
    assert len(restored.execution.reports) == len(state.execution.reports)
    assert len(restored.portfolio.events) == len(state.portfolio.events)
    assert len(restored.market.events) == len(state.market.events)
    assert restored.portfolio.cash.balance("USD") == state.portfolio.cash.balance("USD")


def test_the_module_does_not_reach_the_broker() -> None:
    import ast
    import inspect

    from alphalab.runtime import snapshot as pipeline_snapshot

    tree = ast.parse(inspect.getsource(pipeline_snapshot))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported
    assert not [name for name in imported if name.startswith("alphalab.broker")]


# ---------------------------------------------------------------------------
# Corruption
# ---------------------------------------------------------------------------


def test_a_non_object_payload_is_refused() -> None:
    with pytest.raises(StateDecodeError, match="pipeline snapshot is not an object"):
        from_primitives([])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "key",
    [
        "config",
        "market",
        "strategy",
        "strategy_events",
        "allocation",
        "risk",
        "oms",
        "execution",
        "portfolio",
        "analytics",
        "market_prices",
        "fills",
        "trades",
        "trade_records",
        "portfolio_snapshots",
        "unpriced_assets",
        "id_position",
    ],
)
def test_a_missing_top_level_field_is_refused_naming_it(key: str) -> None:
    state, _, _ = _state()
    payload = _payload(state)
    del payload[key]

    with pytest.raises(StateDecodeError, match=f"missing '{key}'"):
        from_primitives(payload)


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("market_prices", [1], "market_prices is not an object"),
        ("fills", {"nope": 1}, "fills is not an array"),
        ("id_position", "here", "id_position is not an object"),
        ("config", [], "config is not an object"),
        ("risk", 3, "risk is not an object"),
        ("unpriced_assets", [], "unpriced_assets is not an object"),
    ],
)
def test_a_wrongly_typed_top_level_field_is_refused(key: str, value: Any, match: str) -> None:
    state, _, _ = _state()
    payload = _payload(state)
    payload[key] = value

    with pytest.raises(StateDecodeError, match=match):
        from_primitives(payload)


def test_a_malformed_nested_value_names_its_path() -> None:
    state, _, _ = _state()
    payload = _payload(state)
    payload["risk"]["cash"] = "lots"

    with pytest.raises(StateDecodeError, match=r"risk\.cash is not a decimal"):
        from_primitives(payload)


@pytest.mark.parametrize(
    ("path", "subsystem"),
    [
        (("market", "events"), "market"),
        (("risk", "events"), "risk"),
        (("execution", "events"), "execution"),
        (("analytics", "events"), "analytics"),
    ],
)
def test_an_unknown_event_type_is_refused_not_guessed(
    path: tuple[str, str], subsystem: str
) -> None:
    state, _, _ = _state()
    payload = _payload(state)
    events = payload[path[0]][path[1]]
    assert events, "the fixture is meant to populate every one of these logs"
    events[0]["event_type"] = "Teleported"

    with pytest.raises(StateDecodeError, match=f"not a {subsystem} event"):
        from_primitives(payload)


def test_a_malformed_id_position_is_refused() -> None:
    state, _, _ = _state()
    payload = _payload(state)
    payload["id_position"]["draws"] = "many"

    with pytest.raises(StateDecodeError, match=r"id_position\.draws is not an integer"):
        from_primitives(payload)


def test_a_missing_runtime_type_declaration_is_refused() -> None:
    state, _, _ = _state()
    payload = _payload(state)
    del payload["config"]["sizing_model_type"]

    with pytest.raises(StateDecodeError, match="missing 'sizing_model_type'"):
        from_primitives(payload)


# ---------------------------------------------------------------------------
# Field coverage
# ---------------------------------------------------------------------------


def test_the_snapshot_covers_every_pipeline_state_field() -> None:
    """A new durable field must be projected, or declared here as not carried."""

    carried = {field.name for field in fields(PipelineSnapshot)} | {
        # ``strategy`` projects as two fields: the per-strategy records and the
        # runtime event log, because the log is heterogeneous and needs tagging.
        "strategy_events",
    }
    missing = {field.name for field in fields(ExecutionPipelineState)} - carried

    assert not missing, (
        f"ExecutionPipelineState fields absent from PipelineSnapshot: {sorted(missing)}. "
        "Add them to capture/restore/from_primitives, declare them derived, or give "
        "them the supplied-runtime-object treatment."
    )


def test_the_snapshot_carries_nothing_the_state_does_not_have() -> None:
    derived = {"schema_version", "strategy_events"}
    unexpected = (
        {field.name for field in fields(PipelineSnapshot)}
        - {field.name for field in fields(ExecutionPipelineState)}
        - derived
    )

    assert not unexpected, f"PipelineSnapshot invents: {sorted(unexpected)}"


def test_every_config_field_is_carried_or_supplied() -> None:
    from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
    from alphalab.runtime.snapshot import ConfigRecord

    supplied = {"sizing_model", "simulator", "instruments"}
    carried = {field.name for field in fields(ConfigRecord)}
    declared = {f"{name}_type" for name in supplied}

    assert declared <= carried, "each supplied object must record what it was"
    missing = {field.name for field in fields(ExecutionPipelineConfig)} - carried - supplied
    assert not missing, (
        f"ExecutionPipelineConfig fields absent from the snapshot: {sorted(missing)}"
    )


def test_every_strategy_state_field_is_carried_or_supplied() -> None:
    from alphalab.runtime.snapshot import StrategyRecord
    from alphalab.strategy.state import StrategyState

    carried = {field.name for field in fields(StrategyRecord)}
    missing = {field.name for field in fields(StrategyState)} - carried - {"instance"}

    assert not missing, f"StrategyState fields absent from StrategyRecord: {sorted(missing)}"
    assert "instance_type" in carried
    assert "instance" not in carried


def test_the_strategy_record_carries_what_the_strategy_declared() -> None:
    """Schema 2's one addition, guarded the way every other field is."""

    from alphalab.runtime.snapshot import StrategyRecord

    assert "state" in {field.name for field in fields(StrategyRecord)}


def test_the_strategy_state_decoder_reads_every_field_of_its_record() -> None:
    """The state-level guard above does not see inside ``state``.

    ``capture`` projects a declared state as a whole ``StrategyStateRecord``, so
    a new field on it reaches a payload automatically -- and is then silently
    dropped on the way back unless ``_strategy_state_record`` is taught to read
    it. The same gap ``opened_at`` proved for ``Position``.
    """

    import inspect

    from alphalab.runtime.snapshot import StrategyStateRecord, _strategy_state_record

    source = inspect.getsource(_strategy_state_record)
    missing = [f.name for f in fields(StrategyStateRecord) if f'"{f.name}"' not in source]

    assert not missing, (
        f"StrategyStateRecord fields the decoder does not read: {sorted(missing)}. "
        "Add them to _strategy_state_record, or the round trip restores a default."
    )


def test_the_strategy_record_decoder_reads_the_state_field() -> None:
    import inspect

    from alphalab.runtime.snapshot import _strategy_record

    assert '"state"' in inspect.getsource(_strategy_record)


def test_the_run_envelopes_live_outside_the_state_modules_they_project() -> None:
    """ADR-0023 decision 1: two envelopes, each in its owning package's own module.

    Named for what it checks. It was written mid-v2.9 as
    ``test_no_session_or_run_snapshot_exists_yet``, and by the end of that same
    release both envelopes existed -- in ``alphalab.runtime.session_snapshot``
    and ``alphalab.backtesting.snapshot``, which is why the assertions below kept
    passing and stopped meaning what their name said. What they actually pin is
    still worth pinning: ``session.py`` and ``backtesting/state.py`` define the
    states, and neither grows a projection of itself, so a snapshot cannot drift
    away from the module that owns its schema constant.
    """

    import alphalab.backtesting.snapshot as backtest_snapshot
    import alphalab.backtesting.state as backtest_module
    import alphalab.runtime.session as session_module
    import alphalab.runtime.session_snapshot as session_snapshot

    for module in (session_module, backtest_module):
        assert not hasattr(module, "RunSnapshot")
        assert not hasattr(module, "SESSION_SNAPSHOT_SCHEMA")
        assert not hasattr(module, "capture")
        assert not hasattr(module, "restore")

    # And the envelopes are where ADR-0023 put them, one per owning package.
    for module in (session_snapshot, backtest_snapshot):
        assert hasattr(module, "capture")
        assert hasattr(module, "restore")
        assert hasattr(module, "from_primitives")
