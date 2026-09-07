"""A stopped run continues where it left off, and agrees with one that never stopped.

This is the invariant v2.9 exists for, and the last piece of it. The v2.8
archaeology measured the defect: every *quantity* already round-tripped, but a
run that stopped and continued had to re-enter ``id_scope``, which builds a fresh
source positioned at zero, so the continued run re-minted identifiers it had
already used -- up to 4 duplicates in a 41-identifier workload, against zero for
the uninterrupted control, with nothing raising at any layer.

Closing it took the whole release: linear persistence (D1), a versioned OMS
payload (D2), an execution ledger that recognises redelivery (D9), a terminal
route for a working external order (D3), a stream cursor the state owns
(ADR-0022), and then the two envelopes -- ``PipelineSnapshot`` for the execution
core and the session/backtest snapshots here for the layer around it.

The contract is conditional and says so. For a **seeded** run, given the same
records in the same order, the same supplied runtime objects, and
strategy-internal state the caller restores, capture → serialize → deserialize →
restore → continue equals uninterrupted execution across ADR-0023's Class-1 state
and every deterministic identifier. ``StrategyProtocol`` has no state hook, so the
fourth precondition is the caller's and these tests keep it explicitly -- the
scripted strategy used here is a pure function of the event it is given, which is
what makes the precondition satisfiable without inventing object serialization.

The continuation boundary is *between* ``advance`` calls. A session that
processed N records resumes at record N+1 and replays nothing.
"""

import uuid
from dataclasses import fields, replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation.sizing import EqualWeightSizing
from alphalab.backtesting.config import BacktestConfig
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.snapshot import (
    BACKTEST_SNAPSHOT_SCHEMA,
    BacktestObjects,
    BacktestSnapshot,
)
from alphalab.backtesting.snapshot import capture as capture_backtest
from alphalab.backtesting.snapshot import from_primitives as backtest_from_primitives
from alphalab.backtesting.snapshot import restore as restore_backtest
from alphalab.backtesting.state import BacktestState
from alphalab.common.ids import IdStreamPosition, current_id_position, id_scope
from alphalab.execution.fill import FillStatus
from alphalab.execution.policy import ImmediateFill, LiquidityCappedFill
from alphalab.execution.report import ExecutionReport
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.record import MarketRecord
from alphalab.market.source import OrderingGuarantee
from alphalab.oms.status import OrderStatus
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.portfolio.account import Account
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionRouting
from alphalab.runtime.session import (
    ExecutionMode,
    SessionConfig,
    SessionState,
    TradingSession,
)
from alphalab.runtime.session_snapshot import (
    SESSION_SNAPSHOT_SCHEMA,
    SessionObjects,
    SessionSnapshot,
)
from alphalab.runtime.session_snapshot import capture as capture_session
from alphalab.runtime.session_snapshot import from_primitives as session_from_primitives
from alphalab.runtime.session_snapshot import restore as restore_session
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.state import LifecycleState
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

SEED = 20250907
STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())
N, M = 5, 6
TOTAL = N + M


# ---------------------------------------------------------------------------
# One deterministic workload, driven two ways
# ---------------------------------------------------------------------------


def _records(count: int = TOTAL) -> list[MarketRecord]:
    """Uniquely identifiable records, so a replayed one would be visible."""

    return [
        MarketRecord(
            event_id=f"REC-{index}",
            timestamp=2.0 + index,
            payload=sized_quote(ASSET_ID, 2.0 + index, Decimal(100 + index), Decimal("100")),
        )
        for index in range(count)
    ]


def _plan(count: int = TOTAL) -> dict[float, Decimal]:
    return {
        2.0 + index: (Decimal("5") if index % 2 == 0 else Decimal("-3")) for index in range(count)
    }


def _session(seed: int | None = SEED) -> tuple[SessionConfig, ScriptedStrategy]:
    strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, _plan())
    config = SessionConfig(
        pipeline=pipeline_config(STRATEGY_ID),
        mode=ExecutionMode.BACKTEST,
        fill_policy=ImmediateFill(),
        seed=seed,
        start_timestamp=1.0,
    )
    return config, strategy


def _objects(config: SessionConfig, strategy: ScriptedStrategy) -> SessionObjects:
    return SessionObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY_ID: strategy},
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )


def _drive(state: SessionState, records: list[MarketRecord]) -> SessionState:
    for record in records:
        state, _ = TradingSession.advance(state, record, context_factory)
    return state


def _uninterrupted(seed: int | None = SEED) -> SessionState:
    config, strategy = _session(seed)
    with id_scope(seed):
        return _drive(
            TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy)),
            _records(),
        )


def _split(boundary: int = N, seed: int | None = SEED) -> SessionState:
    """Run to ``boundary``, capture, JSON round-trip, restore, resume, finish."""

    config, strategy = _session(seed)
    records = _records()
    with id_scope(seed):
        partial = _drive(
            TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy)),
            records[:boundary],
        )

    payload = serialize(capture_session(partial))
    restored = restore_session(
        session_from_primitives(deserialize(payload)), _objects(config, strategy)
    )

    with TradingSession.resume(restored):
        return _drive(restored, records[boundary:])


def _identifiers(state: SessionState) -> list[str]:
    pipeline = state.pipeline
    return [
        *(str(order.order_id.value) for order in pipeline.oms.orders.orders()),
        *(str(fill.fill_id) for fill in pipeline.fills),
        *(str(trade.trade_id) for trade in pipeline.trades),
        *(report.execution_id for report in pipeline.execution.history),
        *(txn.transaction_id for txn in pipeline.portfolio.ledger.transactions),
        *(event.event_id for event in pipeline.allocation.events),
        *(decision.decision_id for decision in pipeline.risk.history),
        *(event.event_id for event in pipeline.oms.events),
    ]


def _class_one(state: SessionState) -> dict[str, Any]:
    """ADR-0023's Class-1 state: everything that must be identical."""

    p = state.pipeline
    return {
        "positions": {k: v.quantity for k, v in p.portfolio.positions.items()},
        "cash": p.portfolio.cash.balance("USD"),
        "reserved": dict(p.portfolio.cash.reserved),
        "realized_pnl": p.portfolio.realized_pnl,
        "commission": p.portfolio.commission_paid,
        "transactions": p.portfolio.ledger.transactions.to_tuple(),
        "portfolio_events": list(p.portfolio.events),
        "reservations": dict(p.allocation.reservations),
        "contributions": dict(p.allocation.contributions),
        "notional_allocated": p.allocation.notional_allocated,
        "allocation_history": p.allocation.history.to_tuple(),
        "allocation_events": list(p.allocation.events),
        "orders": list(p.oms.orders.orders()),
        "active_orders": set(p.oms.active_orders),
        "completed_orders": set(p.oms.completed_orders),
        "oms_events": list(p.oms.events),
        "execution_reports": dict(p.execution.reports),
        "execution_history": p.execution.history.to_tuple(),
        "risk": (
            p.risk.cash,
            p.risk.buying_power,
            p.risk.peak_nav,
            p.risk.current_nav,
            p.risk.daily_loss,
            p.risk.margin,
            p.risk.exposure,
        ),
        "risk_history": p.risk.history.to_tuple(),
        "analytics": p.analytics.reports,
        "equity_curve": p.portfolio_snapshots.to_tuple(),
        "strategy": {
            k: (v.status, v.config, v.subscriptions, v.last_error)
            for k, v in p.strategy.strategies.items()
        },
        "strategy_events": p.strategy.events,
        "latest_quotes": dict(p.market.latest_quotes),
        "market_history": p.market.history.to_tuple(),
        "market_prices": dict(p.market_prices),
        "fills": p.fills.to_tuple(),
        "trades": p.trades.to_tuple(),
        "trade_records": p.trade_records.to_tuple(),
        "unpriced_assets": dict(p.unpriced_assets),
        "id_position": p.id_position,
        "processed": state.processed,
        "current_timestamp": state.current_timestamp,
        "last_record_timestamp": state.last_record_timestamp,
        "skipped": state.skipped.to_tuple(),
        "source_id": state.source_id,
    }


# ---------------------------------------------------------------------------
# THE invariant
# ---------------------------------------------------------------------------


def test_the_workload_is_substantial_enough_to_mean_something() -> None:
    control = _uninterrupted()

    assert control.processed == TOTAL
    assert len(list(control.pipeline.oms.orders.orders())) == TOTAL
    assert len(control.pipeline.fills) == TOTAL
    assert len(_identifiers(control)) > 100
    assert control.pipeline.id_position.seed == SEED
    assert control.pipeline.id_position.draws > 0


def test_a_resumed_run_equals_an_uninterrupted_one() -> None:
    """The v2.9 acceptance invariant, across ADR-0023's Class-1 state."""

    assert _class_one(_split()) == _class_one(_uninterrupted())


def test_a_resumed_run_mints_the_same_identifiers() -> None:
    control, resumed = _uninterrupted(), _split()

    assert _identifiers(resumed) == _identifiers(control)


def test_a_resumed_run_produces_no_duplicate_identifier() -> None:
    """The v2.8 defect, measured then and asserted absent now."""

    identifiers = _identifiers(_split())

    assert len(set(identifiers)) == len(identifiers)


@pytest.mark.parametrize("boundary", range(1, TOTAL))
def test_every_resume_boundary_reproduces_the_control(boundary: int) -> None:
    control = _uninterrupted()
    resumed = _split(boundary)

    assert _class_one(resumed) == _class_one(control), f"boundary {boundary} diverged"
    assert _identifiers(resumed) == _identifiers(control)
    assert len(set(_identifiers(resumed))) == len(_identifiers(resumed))


def test_no_record_is_processed_twice() -> None:
    """Continuation starts at N+1, never by replaying N."""

    control, resumed = _uninterrupted(), _split()

    assert resumed.processed == control.processed == TOTAL
    assert len(resumed.pipeline.portfolio_snapshots) == TOTAL + 1, "one per record, plus funding"
    assert len(resumed.pipeline.market.events) == TOTAL
    orders = [str(o.order_id.value) for o in resumed.pipeline.oms.orders.orders()]
    assert len(orders) == len(set(orders)) == TOTAL


def test_an_unseeded_run_round_trips_without_claiming_identifier_continuity() -> None:
    """The approved carve-out: quantities and bookkeeping, not identifiers."""

    control, resumed = _uninterrupted(seed=None), _split(seed=None)

    assert resumed.pipeline.id_position == IdStreamPosition(None, 0)
    assert control.pipeline.portfolio.cash.balance(
        "USD"
    ) == resumed.pipeline.portfolio.cash.balance("USD")
    assert resumed.processed == control.processed == TOTAL
    assert len(set(_identifiers(resumed))) == len(_identifiers(resumed))
    assert _identifiers(resumed) != _identifiers(control), "uuid4 cannot repeat across runs"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("constant", "expected"),
    [(SESSION_SNAPSHOT_SCHEMA, 1), (BACKTEST_SNAPSHOT_SCHEMA, 1)],
)
def test_the_schema_constants_are_one(constant: int, expected: int) -> None:
    assert constant == expected


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("alphalab.runtime.session_snapshot", "SESSION_SNAPSHOT_SCHEMA"),
        ("alphalab.backtesting.snapshot", "BACKTEST_SNAPSHOT_SCHEMA"),
    ],
)
def test_the_constants_are_not_aliases_of_the_shared_default(module: str, name: str) -> None:
    import importlib
    import inspect

    loaded = importlib.import_module(module)
    source = inspect.getsource(loaded)

    assert not hasattr(loaded, "DEFAULT_SCHEMA_VERSION")
    assert f"{name}: Final = 1" in source
    assert "= DEFAULT_SCHEMA_VERSION" not in source


def test_session_capture_declares_the_version() -> None:
    state = _uninterrupted()
    payload = deserialize(serialize(capture_session(state)))

    assert capture_session(state).schema_version == SESSION_SNAPSHOT_SCHEMA
    assert payload["schema_version"] == 1
    # The nested core moved to 2 in v2.10 and this envelope did not: the whole
    # point of ADR-0023 decision 1's split, exercised for the first time.
    assert payload["pipeline"]["schema_version"] == 2


def test_a_missing_session_version_is_refused_with_no_legacy_path() -> None:
    payload = dict(deserialize(serialize(capture_session(_uninterrupted()))))
    del payload["schema_version"]

    with pytest.raises(StateDecodeError, match="missing 'schema_version'"):
        session_from_primitives(payload)


@pytest.mark.parametrize("version", [2, 99, 0, -1])
def test_an_unreadable_session_version_is_refused(version: int) -> None:
    payload = dict(deserialize(serialize(capture_session(_uninterrupted()))))
    payload["schema_version"] = version

    with pytest.raises(StateDecodeError, match=f"declares schema version {version}"):
        session_from_primitives(payload)


@pytest.mark.parametrize("version", ["1", 1.0, True, None])
def test_a_malformed_session_version_is_refused(version: object) -> None:
    payload = dict(deserialize(serialize(capture_session(_uninterrupted()))))
    payload["schema_version"] = version

    with pytest.raises(StateDecodeError, match="schema_version is not an integer"):
        session_from_primitives(payload)


def test_the_refusal_names_the_session_subsystem() -> None:
    payload = dict(deserialize(serialize(capture_session(_uninterrupted()))))
    payload["schema_version"] = 2

    with pytest.raises(StateDecodeError) as excinfo:
        session_from_primitives(payload)

    assert str(excinfo.value).startswith("session snapshot")


def test_a_nested_pipeline_failure_arrives_through_the_pipeline_decoder() -> None:
    """Not normalized into a generic session error."""

    payload = dict(deserialize(serialize(capture_session(_uninterrupted()))))
    payload["pipeline"]["schema_version"] = 3

    with pytest.raises(StateDecodeError, match="pipeline snapshot declares schema version 3"):
        session_from_primitives(payload)


def test_a_nested_oms_failure_keeps_its_own_error_type() -> None:
    from alphalab.oms.snapshot import SnapshotDecodeError

    payload = dict(deserialize(serialize(capture_session(_uninterrupted()))))
    payload["pipeline"]["oms"]["schema_version"] = 2

    with pytest.raises(SnapshotDecodeError, match="oms snapshot declares schema version 2"):
        session_from_primitives(payload)


# ---------------------------------------------------------------------------
# Round trip, both states
# ---------------------------------------------------------------------------


def test_a_session_round_trips_in_memory_and_across_json() -> None:
    config, strategy = _session()
    with id_scope(SEED):
        state = _drive(
            TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy)),
            _records(),
        )
    objects = _objects(config, strategy)

    assert restore_session(capture_session(state), objects) == state
    payload = serialize(capture_session(state))
    assert restore_session(session_from_primitives(deserialize(payload)), objects) == state


def test_session_serialization_is_deterministic_and_stable() -> None:
    config, strategy = _session()
    with id_scope(SEED):
        state = _drive(
            TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy)),
            _records(),
        )
    objects = _objects(config, strategy)
    payload = serialize(capture_session(state))
    restored = restore_session(session_from_primitives(deserialize(payload)), objects)

    assert payload == serialize(capture_session(state))
    assert capture_session(restored) == capture_session(state)
    assert serialize(capture_session(restored)) == payload


def _backtest() -> tuple[BacktestState, BacktestObjects, BacktestConfig, ScriptedStrategy]:
    strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, _plan())
    config = BacktestConfig(
        pipeline=pipeline_config(STRATEGY_ID),
        fill_policy=ImmediateFill(),
        seed=SEED,
        start_timestamp=1.0,
    )
    with id_scope(SEED):
        state = BacktestEngine.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
        for record in _records():
            state, _ = BacktestEngine.advance(state, record, context_factory)
    objects = BacktestObjects(
        pipeline=RuntimeObjects(
            config.pipeline.sizing_model,
            config.pipeline.simulator,
            {STRATEGY_ID: strategy},
            config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )
    return state, objects, config, strategy


def test_a_backtest_round_trips_in_memory_and_across_json() -> None:
    state, objects, _, _ = _backtest()

    assert restore_backtest(capture_backtest(state), objects) == state
    payload = serialize(capture_backtest(state))
    assert restore_backtest(backtest_from_primitives(deserialize(payload)), objects) == state


def test_the_backtest_step_history_survives_intact() -> None:
    state, objects, _, _ = _backtest()
    payload = serialize(capture_backtest(state))
    restored = restore_backtest(backtest_from_primitives(deserialize(payload)), objects)

    assert len(restored.steps) == TOTAL
    assert restored.steps.to_tuple() == state.steps.to_tuple()
    assert [step.event_id for step in restored.steps] == [f"REC-{i}" for i in range(TOTAL)]
    assert [step.index for step in restored.steps] == list(range(TOTAL))
    assert all(step.orders for step in restored.steps)
    assert all(step.fills for step in restored.steps)


def test_a_backtest_resumes_the_same_way_a_session_does() -> None:
    """Both engines drive the same canonical step, so both resume."""

    strategy_a = ScriptedStrategy(STRATEGY_ID, ASSET_ID, _plan())
    config = BacktestConfig(
        pipeline=pipeline_config(STRATEGY_ID),
        fill_policy=ImmediateFill(),
        seed=SEED,
        start_timestamp=1.0,
    )
    with id_scope(SEED):
        control = BacktestEngine.initialize(config, running_strategy_state(STRATEGY_ID, strategy_a))
        for record in _records():
            control, _ = BacktestEngine.advance(control, record, context_factory)

    strategy_b = ScriptedStrategy(STRATEGY_ID, ASSET_ID, _plan())
    records = _records()
    with id_scope(SEED):
        partial = BacktestEngine.initialize(config, running_strategy_state(STRATEGY_ID, strategy_b))
        for record in records[:N]:
            partial, _ = BacktestEngine.advance(partial, record, context_factory)

    objects = BacktestObjects(
        pipeline=RuntimeObjects(
            config.pipeline.sizing_model,
            config.pipeline.simulator,
            {STRATEGY_ID: strategy_b},
            config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )
    restored = restore_backtest(
        backtest_from_primitives(deserialize(serialize(capture_backtest(partial)))), objects
    )
    with BacktestEngine.resume(restored):
        for record in records[N:]:
            restored, _ = BacktestEngine.advance(restored, record, context_factory)

    assert restored.processed == control.processed == TOTAL
    assert restored.steps.to_tuple() == control.steps.to_tuple()
    assert restored.pipeline.portfolio == control.pipeline.portfolio
    assert restored.pipeline.id_position == control.pipeline.id_position
    assert [str(o.order_id.value) for o in restored.pipeline.oms.orders.orders()] == [
        str(o.order_id.value) for o in control.pipeline.oms.orders.orders()
    ]


# ---------------------------------------------------------------------------
# The resume scope
# ---------------------------------------------------------------------------


def test_resume_installs_the_source_the_restored_position_implies() -> None:
    config, strategy = _session()
    records = _records()
    with id_scope(SEED):
        partial = _drive(
            TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy)),
            records[:N],
        )
    restored = restore_session(capture_session(partial), _objects(config, strategy))

    assert current_id_position() == IdStreamPosition(None, 0), "nothing installed yet"
    with TradingSession.resume(restored):
        assert current_id_position() == restored.pipeline.id_position
    assert current_id_position() == IdStreamPosition(None, 0), "and restored on exit"


def test_resume_of_an_unseeded_run_installs_no_deterministic_source() -> None:
    config, strategy = _session(seed=None)
    with id_scope(None):
        state = TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
    restored = restore_session(capture_session(state), _objects(config, strategy))

    with TradingSession.resume(restored):
        assert current_id_position() == IdStreamPosition(None, 0)


def test_restore_itself_installs_nothing_and_mints_nothing() -> None:
    config, strategy = _session()
    with id_scope(SEED):
        state = _drive(
            TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy)),
            _records(),
        )
    payload = serialize(capture_session(state))
    objects = _objects(config, strategy)

    with id_scope(SEED):
        before = current_id_position()
        restored = restore_session(session_from_primitives(deserialize(payload)), objects)
        after = current_id_position()

    assert before == after == IdStreamPosition(SEED, 0)
    assert _identifiers(restored) == _identifiers(state)


# ---------------------------------------------------------------------------
# Runtime object rebinding
# ---------------------------------------------------------------------------


def _snapshot_and_objects() -> tuple[SessionSnapshot, SessionObjects]:
    config, strategy = _session()
    with id_scope(SEED):
        state = _drive(
            TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy)),
            _records(3),
        )
    payload = serialize(capture_session(state))
    return session_from_primitives(deserialize(payload)), _objects(config, strategy)


def test_the_correct_objects_resume_successfully() -> None:
    snapshot, objects = _snapshot_and_objects()

    assert restore_session(snapshot, objects).processed == 3


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"fill_policy": None}, "No fill policy supplied"),
        ({"fill_policy": LiquidityCappedFill()}, "fill policy supplied is a LiquidityCappedFill"),
    ],
)
def test_a_missing_or_wrong_fill_policy_is_refused(overrides: dict[str, Any], match: str) -> None:
    snapshot, objects = _snapshot_and_objects()

    with pytest.raises(StateDecodeError, match=match):
        restore_session(snapshot, replace(objects, **overrides))


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"sizing_model": None}, "No sizing model supplied"),
        ({"simulator": None}, "No simulator supplied"),
        ({"strategies": {}}, "No strategy instance for"),
        ({"sizing_model": EqualWeightSizing(3)}, "sizing model supplied is a"),
    ],
)
def test_a_missing_or_wrong_pipeline_object_is_refused_through_the_session(
    overrides: dict[str, Any], match: str
) -> None:
    snapshot, objects = _snapshot_and_objects()

    with pytest.raises(StateDecodeError, match=match):
        restore_session(snapshot, replace(objects, pipeline=replace(objects.pipeline, **overrides)))


def test_no_module_constructs_anything_from_a_recorded_type_name() -> None:
    import ast
    import inspect

    from alphalab.backtesting import snapshot as backtest_snapshot
    from alphalab.runtime import session_snapshot

    for module in (session_snapshot, backtest_snapshot):
        source = inspect.getsource(module)
        called = {
            node.func.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "__import__" not in called
        assert "eval" not in called
        assert "getattr" not in called
        assert "importlib" not in source


# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


def test_a_restored_session_is_refused_when_its_currencies_disagree() -> None:
    """The pipeline's own restore re-applies it; the session does not bypass it."""

    _, objects = _snapshot_and_objects()
    payload = dict(deserialize(serialize(capture_session(_uninterrupted()))))
    payload["pipeline"]["config"]["account"]["base_currency"] = "EUR"

    config, strategy = _session()
    disagreeing = replace(config.pipeline, account=Account("acct", "EUR", "n", 1.0))
    with pytest.raises(RuntimeValidationError) as from_initialize:
        ExecutionPipeline.initialize(
            disagreeing, running_strategy_state(STRATEGY_ID, strategy), 1.0
        )

    with pytest.raises(RuntimeValidationError) as from_restore:
        restore_session(session_from_primitives(payload), objects)

    assert str(from_restore.value) == str(from_initialize.value)


# ---------------------------------------------------------------------------
# The earlier steps still hold after a restore
# ---------------------------------------------------------------------------


def _live_session() -> tuple[SessionState, SessionObjects, tuple[Any, ...]]:
    """A live-routed session holding working external orders."""

    strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, {2.0: Decimal("10"), 3.0: Decimal("6")})
    config = SessionConfig(
        pipeline=replace(pipeline_config(STRATEGY_ID), routing=ExecutionRouting.EXTERNAL),
        mode=ExecutionMode.LIVE,
        fill_policy=ImmediateFill(),
        seed=SEED,
        start_timestamp=1.0,
    )
    with id_scope(SEED):
        state = TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
        state = _drive(state, _records(2))
    return state, _objects(config, strategy), tuple(state.pipeline.oms.orders.open_orders())


def test_a_working_external_order_survives_a_session_round_trip_and_terminalizes() -> None:
    state, objects, orders = _live_session()
    payload = serialize(capture_session(state))
    restored = restore_session(session_from_primitives(deserialize(payload)), objects)

    target = orders[0]
    key = str(target.order_id.value)
    untouched = {other for other in restored.pipeline.allocation.reservations if other != key}

    assert restored.pipeline.oms.orders.find(target.order_id).is_open
    assert key in restored.pipeline.allocation.reservations
    assert key in restored.pipeline.allocation.contributions

    terminated = ExecutionPipeline.apply_terminal_outcome(
        restored.pipeline,
        restored.pipeline.oms.orders.find(target.order_id),
        OrderStatus.CANCELLED,
        9.0,
    )

    assert terminated.oms.orders.find(target.order_id).status is OrderStatus.CANCELLED
    assert key not in terminated.allocation.reservations
    assert key not in terminated.allocation.contributions
    assert set(terminated.allocation.reservations) == untouched


def test_a_duplicate_execution_is_still_a_no_op_after_a_session_round_trip() -> None:
    state, objects, orders = _live_session()
    report = ExecutionReport(
        execution_id=str(uuid.uuid4()),
        order_id=str(orders[0].order_id.value),
        asset_id=ASSET_ID,
        strategy_id="",
        timestamp=6.0,
        fill_price=Decimal("100"),
        fill_quantity=Decimal("4"),
        commission=Decimal("0"),
        slippage=Decimal("0"),
        liquidity_flag="",
        venue="LIVE",
        currency="USD",
        status=FillStatus.PARTIAL_FILL,
    )
    applied, _, _ = ExecutionPipeline.apply_execution_report(state.pipeline, orders[0], report)
    state = replace(state, pipeline=applied)

    payload = serialize(capture_session(state))
    restored = restore_session(session_from_primitives(deserialize(payload)), objects)

    assert report.execution_id in restored.pipeline.execution.reports

    again, fills, trades = ExecutionPipeline.apply_execution_report(
        restored.pipeline, restored.pipeline.oms.orders.find(orders[0].order_id), report
    )

    assert fills == ()
    assert trades == ()
    assert again is restored.pipeline


def test_a_failed_strategy_stays_failed_across_a_restore() -> None:
    """Restore preserves the state; it does not quietly reset an error."""

    from alphalab.strategy.supervisor import RuntimeSupervisor

    config, strategy = _session()
    with id_scope(SEED):
        state = TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
    runtime = state.pipeline.strategy
    failed, _ = RuntimeSupervisor.fail(runtime.strategies[STRATEGY_ID], "boom", 2.0)
    state = replace(
        state,
        pipeline=replace(
            state.pipeline, strategy=replace(runtime, strategies={STRATEGY_ID: failed})
        ),
    )

    payload = serialize(capture_session(state))
    restored = restore_session(
        session_from_primitives(deserialize(payload)), _objects(config, strategy)
    )
    decoded = restored.pipeline.strategy.strategies[STRATEGY_ID]

    assert decoded.status is LifecycleState.FAILED
    assert decoded.last_error == "boom"
    assert decoded.instance is strategy


def test_skipped_records_survive_and_continuation_stays_at_the_boundary() -> None:
    strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, _plan())
    config = SessionConfig(
        pipeline=pipeline_config(STRATEGY_ID),
        mode=ExecutionMode.PAPER,
        fill_policy=ImmediateFill(),
        seed=SEED,
        start_timestamp=1.0,
        ordering=OrderingGuarantee.UNORDERED,
    )
    records = _records(4)
    backwards = MarketRecord(
        event_id="REC-BACKWARDS",
        timestamp=1.5,
        payload=sized_quote(ASSET_ID, 1.5, Decimal("99"), Decimal("100")),
    )
    with id_scope(SEED):
        state = TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
        state = _drive(state, records[:2])
        state, result = TradingSession.advance(state, backwards, context_factory)

    assert result is None
    assert len(state.skipped) == 1
    assert state.skipped[0].record.event_id == "REC-BACKWARDS"
    processed_before = state.processed

    payload = serialize(capture_session(state))
    restored = restore_session(
        session_from_primitives(deserialize(payload)), _objects(config, strategy)
    )

    assert restored.skipped.to_tuple() == state.skipped.to_tuple()
    assert restored.processed == processed_before == 2
    assert restored.last_record_timestamp == state.last_record_timestamp
    assert restored.config.ordering is OrderingGuarantee.UNORDERED

    with TradingSession.resume(restored):
        restored, _ = TradingSession.advance(restored, records[2], context_factory)

    assert restored.processed == 3
    assert len(restored.skipped) == 1


def test_a_chronological_session_still_refuses_a_regressing_record_after_restore() -> None:
    config, strategy = _session()
    with id_scope(SEED):
        state = _drive(
            TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy)),
            _records(3),
        )
    restored = restore_session(capture_session(state), _objects(config, strategy))
    backwards = MarketRecord(
        event_id="OLD",
        timestamp=1.5,
        payload=sized_quote(ASSET_ID, 1.5, Decimal("99"), Decimal("100")),
    )

    with TradingSession.resume(restored), pytest.raises(MarketValidationError):
        TradingSession.advance(restored, backwards, context_factory)


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_the_restored_session_is_independent_of_the_captured_one() -> None:
    config, strategy = _session()
    records = _records()
    with id_scope(SEED):
        original = _drive(
            TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy)),
            records[:N],
        )
    before = capture_session(original)
    restored = restore_session(capture_session(original), _objects(config, strategy))

    with TradingSession.resume(restored):
        advanced = _drive(restored, records[N:])

    assert advanced.processed == TOTAL
    assert original.processed == N, "the captured state is untouched"
    assert capture_session(original) == before


# ---------------------------------------------------------------------------
# Corruption
# ---------------------------------------------------------------------------


def test_a_non_object_session_payload_is_refused() -> None:
    with pytest.raises(StateDecodeError, match="session snapshot is not an object"):
        session_from_primitives([])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "key",
    [
        "pipeline",
        "mode",
        "seed",
        "start_timestamp",
        "max_market_data_age_seconds",
        "ordering",
        "fill_policy_type",
        "processed",
        "current_timestamp",
        "skipped",
        "last_record_timestamp",
        "source_id",
    ],
)
def test_a_missing_session_field_is_refused_naming_it(key: str) -> None:
    payload = dict(deserialize(serialize(capture_session(_uninterrupted()))))
    del payload[key]

    with pytest.raises(StateDecodeError, match=f"missing '{key}'"):
        session_from_primitives(payload)


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("processed", "five", "processed is not an integer"),
        ("pipeline", [], "pipeline is not an object"),
        ("skipped", {"nope": 1}, "skipped is not an array"),
        ("mode", "ExecutionMode.TELEPORT", "mode names no ExecutionMode member"),
        ("current_timestamp", "now", "current_timestamp is not a number"),
    ],
)
def test_a_wrongly_typed_session_field_is_refused(key: str, value: Any, match: str) -> None:
    payload = dict(deserialize(serialize(capture_session(_uninterrupted()))))
    payload[key] = value

    with pytest.raises(StateDecodeError, match=match):
        session_from_primitives(payload)


def test_a_malformed_skipped_record_is_refused() -> None:
    strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, {})
    config = SessionConfig(
        pipeline=pipeline_config(STRATEGY_ID),
        mode=ExecutionMode.PAPER,
        fill_policy=ImmediateFill(),
        seed=SEED,
        start_timestamp=1.0,
        max_market_data_age_seconds=0.5,
    )
    with id_scope(SEED):
        state = TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
        state, _ = TradingSession.advance(state, _records(1)[0], context_factory, now=100.0)

    assert len(state.skipped) == 1
    payload = dict(deserialize(serialize(capture_session(state))))
    payload["skipped"][0]["payload_type"] = "Teleport"

    with pytest.raises(StateDecodeError, match="not a market payload"):
        session_from_primitives(payload)


def test_a_missing_backtest_field_is_refused_naming_it() -> None:
    state, _, _, _ = _backtest()
    payload = dict(deserialize(serialize(capture_backtest(state))))
    del payload["steps"]

    with pytest.raises(StateDecodeError, match="missing 'steps'"):
        backtest_from_primitives(payload)


def test_a_malformed_backtest_step_is_refused() -> None:
    state, _, _, _ = _backtest()
    payload = dict(deserialize(serialize(capture_backtest(state))))
    payload["steps"][0]["equity"] = "lots"

    with pytest.raises(StateDecodeError, match="equity is not a decimal"):
        backtest_from_primitives(payload)


# ---------------------------------------------------------------------------
# Field coverage
# ---------------------------------------------------------------------------


def test_the_session_snapshot_covers_every_session_state_field() -> None:
    carried = {field.name for field in fields(SessionSnapshot)} | {
        # SessionConfig is flattened: its pipeline half is the nested pipeline
        # snapshot's config, and its own fields are carried individually.
        "config",
    }
    missing = {field.name for field in fields(SessionState)} - carried

    assert not missing, f"SessionState fields absent from SessionSnapshot: {sorted(missing)}"


def test_every_session_config_field_is_carried_or_supplied() -> None:
    carried = {field.name for field in fields(SessionSnapshot)}
    missing = (
        {field.name for field in fields(SessionConfig)}
        - carried
        - {"pipeline", "fill_policy"}  # nested snapshot's config; supplied object
    )

    assert not missing, f"SessionConfig fields absent from SessionSnapshot: {sorted(missing)}"
    assert "fill_policy_type" in carried


def test_the_backtest_snapshot_covers_every_backtest_state_field() -> None:
    carried = {field.name for field in fields(BacktestSnapshot)} | {"config"}
    missing = {field.name for field in fields(BacktestState)} - carried

    assert not missing, f"BacktestState fields absent from BacktestSnapshot: {sorted(missing)}"


def test_every_backtest_config_field_is_carried_or_supplied() -> None:
    carried = {field.name for field in fields(BacktestSnapshot)}
    missing = (
        {field.name for field in fields(BacktestConfig)} - carried - {"pipeline", "fill_policy"}
    )

    assert not missing, f"BacktestConfig fields absent from BacktestSnapshot: {sorted(missing)}"
    assert "fill_policy_type" in carried


def test_the_pipeline_configuration_is_carried_exactly_once() -> None:
    """Two copies could disagree about the run's own configuration."""

    payload = dict(deserialize(serialize(capture_session(_uninterrupted()))))

    assert "config" in payload["pipeline"]
    assert "pipeline_config" not in payload
    assert "config" not in payload
