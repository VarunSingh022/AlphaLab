"""The run runtime has one owner, and the execution core did not move.

ADR-0030 unifies the run layer: ``SessionState`` and ``BacktestState`` become one
:class:`~alphalab.runtime.run.RunState` owned by
:class:`~alphalab.runtime.run.RunEngine`, their two snapshot envelopes become
one, and :class:`~alphalab.runtime.session.TradingSession`,
:class:`~alphalab.backtesting.engine.BacktestEngine` and
:class:`~alphalab.backtesting.replay.ReplayBacktest` become drivers.

What this file holds is the half of that decision a reader cannot see from a
passing behavioural test: that the reshape reached the run layer and **stopped
there**. ``ExecutionPipelineState`` gains no field, ``PIPELINE_SNAPSHOT_SCHEMA``
does not move, ``alphalab.persistence`` is untouched, and exactly one function in
the repository derives an identifier source from a pipeline's stream position.
The last of those is the one that was actually broken: v2.13 had two, and they
were character-identical.
"""

import ast
import dataclasses
import importlib
import inspect
import pathlib
from typing import Any

import pytest

from alphalab.allocation.snapshot import ALLOCATION_SNAPSHOT_SCHEMA
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.replay import ReplayBacktest
from alphalab.backtesting.state import BacktestResult, ReplayResult
from alphalab.common.constants import DEFAULT_SCHEMA_VERSION
from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA
from alphalab.oms.snapshot import OMS_SNAPSHOT_SCHEMA
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.run_state import RUN_STATE_ENVELOPE_SCHEMA
from alphalab.portfolio.snapshot import PORTFOLIO_SNAPSHOT_SCHEMA
from alphalab.runtime.execution_pipeline import (
    ExecutionPipelineConfig,
    ExecutionPipelineState,
)
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine, RunState, RunStep
from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA, RunObjects, RunSnapshot
from alphalab.runtime.session import TradingSession
from alphalab.runtime.snapshot import PIPELINE_SNAPSHOT_SCHEMA, READABLE_PIPELINE_SCHEMAS
from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "alphalab"


def _sources() -> list[tuple[pathlib.Path, str]]:
    return [
        (path, path.read_text())
        for path in sorted(PACKAGE.rglob("*.py"))
        if "__pycache__" not in str(path)
    ]


# --------------------------------------------------------------------------- #
# A. The execution core did not move
# --------------------------------------------------------------------------- #


def test_the_pipeline_state_gains_no_field() -> None:
    """The whole performance budget of this release, expressed as a number.

    ``ExecutionPipelineState`` is reconstructed eleven times per trading record,
    so every field added to it is paid eleven times. Measured on the development
    machine, widening it from 16 to 20 fields costs 5.6% of a trading record and
    to 24 fields costs 14.0% -- which is why the run layer is a state *around*
    this one and not fields *on* it. See ADR-0030.
    """

    assert len(dataclasses.fields(ExecutionPipelineState)) == 16


def test_the_pipeline_schema_does_not_move() -> None:
    """ADR-0023 decision 1 split the envelopes so this release would not move it.

    That split is being spent here, exactly as designed: the run layer moves and
    the stable core does not.
    """

    assert PIPELINE_SNAPSHOT_SCHEMA == 2
    assert READABLE_PIPELINE_SCHEMAS == (1, 2)


@pytest.mark.parametrize(
    ("constant", "value"),
    [
        (ALLOCATION_SNAPSHOT_SCHEMA, 1),
        (OMS_SNAPSHOT_SCHEMA, 1),
        (PORTFOLIO_SNAPSHOT_SCHEMA, 2),
        (LIFECYCLE_SNAPSHOT_SCHEMA, 1),
        (RUN_STATE_ENVELOPE_SCHEMA, 1),
        (DEFAULT_SCHEMA_VERSION, 1),
    ],
)
def test_no_other_schema_constant_moves(constant: int, value: int) -> None:
    """Only the run envelope moves, and only because its state did."""

    assert constant == value


def test_the_run_envelope_is_the_only_new_constant() -> None:
    assert RUN_SNAPSHOT_SCHEMA == 1

    source = inspect.getsource(importlib.import_module("alphalab.runtime.run_snapshot"))
    assert "RUN_SNAPSHOT_SCHEMA: Final = 1" in source
    assert "= DEFAULT_SCHEMA_VERSION" not in source


# --------------------------------------------------------------------------- #
# B. One owner of continuation
# --------------------------------------------------------------------------- #


def test_exactly_one_function_derives_an_id_source_from_a_stream_position() -> None:
    """The defect this release exists for, pinned so it cannot come back.

    v2.13 had two implementations of continuation --
    ``TradingSession.resume`` and ``BacktestEngine.resume`` -- and they were
    character-identical::

        return use_id_source(id_source_for(state.pipeline.id_position))

    Two owners of one determinism contract is a contract that can drift. There
    is now one, and a second would fail here rather than in a run somebody
    compares months later.
    """

    def calls(source: str) -> int:
        return sum(
            1
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "id_source_for"
        )

    sites = [
        (str(path.relative_to(PACKAGE.parent)), calls(source))
        for path, source in _sources()
        if path.name != "ids.py" and calls(source)
    ]

    assert sites == [("alphalab/runtime/run.py", 1)], "one call, in RunEngine.resume"
    assert "id_source_for" in RunEngine.resume.__code__.co_names


def test_every_driver_resume_delegates_rather_than_reimplementing() -> None:
    for resume in (TradingSession.resume, BacktestEngine.resume):
        names = resume.__code__.co_names
        assert "RunEngine" in names and "resume" in names
        assert "id_source_for" not in names


# --------------------------------------------------------------------------- #
# C. Drivers hold no run state
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("driver", [TradingSession, BacktestEngine, ReplayBacktest])
def test_a_driver_is_stateless(driver: type) -> None:
    """A driver is a set of static methods over ``RunState``, not a state itself."""

    assert not dataclasses.is_dataclass(driver)
    assert not any(
        isinstance(inspect.getattr_static(driver, name), property)
        for name in vars(driver)
        if not name.startswith("__")
    )


def test_no_second_run_state_type_survives() -> None:
    """``SessionState`` and ``BacktestState`` are removed, not aliased."""

    for module_name, names in (
        ("alphalab.runtime.session", ("SessionState", "SessionConfig", "SkippedRecord")),
        ("alphalab.backtesting.state", ("BacktestState", "BacktestStep", "BacktestConfig")),
        ("alphalab.backtesting", ("BacktestState", "BacktestStep", "BacktestConfig")),
    ):
        module = importlib.import_module(module_name)
        for name in names:
            assert not hasattr(module, name), f"{module_name}.{name} survived as an alias"

    for gone in (
        "alphalab.backtesting.config",
        "alphalab.backtesting.snapshot",
        "alphalab.runtime.session_snapshot",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(gone)


def test_the_result_is_a_projection_and_not_a_copy() -> None:
    """One stored field; everything else reads through the finished run."""

    assert [f.name for f in dataclasses.fields(BacktestResult)] == ["run"]
    for name in ("config", "state", "steps", "records_processed", "seed", "dataset_id"):
        assert isinstance(getattr(BacktestResult, name), property)
    assert isinstance(ReplayResult.dataset_id, property)


# --------------------------------------------------------------------------- #
# D. Every driver declares its environment
# --------------------------------------------------------------------------- #


def test_run_config_requires_a_mode() -> None:
    """A run that cannot say which environment it is in cannot say what it captured."""

    mode = next(f for f in dataclasses.fields(RunConfig) if f.name == "mode")
    assert mode.default is dataclasses.MISSING
    assert mode.default_factory is dataclasses.MISSING


def test_the_mode_and_the_routing_cannot_disagree() -> None:
    from alphalab.runtime.execution_pipeline import ExecutionRouting

    for mode in ExecutionMode:
        config = RunConfig(pipeline=_pipeline_config(), mode=mode)
        assert config.pipeline.routing is mode.routing

    assert ExecutionMode.LIVE.routing is ExecutionRouting.EXTERNAL
    assert ExecutionMode.BACKTEST.routing is ExecutionRouting.SIMULATED


def test_the_backtest_and_replay_modes_are_no_longer_dead() -> None:
    """Until v2.14 nothing in the package set either, so a captured backtest and
    a captured replay wrote identical payloads."""

    assert "BACKTEST" in inspect.getsource(BacktestEngine.run)
    assert "REPLAY" in inspect.getsource(ReplayBacktest.run)


def _pipeline_config() -> ExecutionPipelineConfig:
    from tests.integration.harness import pipeline_config

    return pipeline_config("BOUNDARY")


# --------------------------------------------------------------------------- #
# E. Persistence is untouched and stays payload-agnostic
# --------------------------------------------------------------------------- #


def test_the_store_names_no_domain_type() -> None:
    """ADR-0029 decision 2 was written for this release. It holds.

    A store with ``save_session(state)`` and ``save_backtest(state)`` would have
    had to be redesigned here. This one could not even see the change.
    """

    domain = {
        "RunState",
        "RunSnapshot",
        "RunConfig",
        "RunStep",
        "SessionState",
        "BacktestState",
        "PipelineSnapshot",
    }

    for module in ("alphalab.persistence.run_store", "alphalab.persistence.run_state"):
        tree = ast.parse(inspect.getsource(importlib.import_module(module)))
        # Identifiers only. ``run_store``'s own docstring *discusses*
        # ``SessionState`` -- it is the paragraph explaining why this protocol
        # cannot be reached by a release that reshapes it -- and prose is not a
        # dependency. What matters is that no name here is bound or called.
        named = (
            {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            | {
                alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom | ast.Import)
                for alias in node.names
            }
        )

        assert not (named & domain), f"{module} names {sorted(named & domain)}"


def test_no_persistence_module_imports_the_runtime() -> None:
    for path, source in _sources():
        if path.parent.name != "persistence":
            continue
        imported = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        offenders = {
            m for m in imported if m.startswith(("alphalab.runtime", "alphalab.backtesting"))
        }
        assert not offenders, f"{path.name} imports {offenders}"


# --------------------------------------------------------------------------- #
# F. The canonical path never touches the deprecated state machine
# --------------------------------------------------------------------------- #


ORPHAN_MODULES = frozenset(
    f"alphalab.runtime.{name}"
    for name in (
        "engine",
        "dispatcher",
        "supervisor",
        "events",
        "validation",
        "state",
        "views",
        "metrics",
        "runtime",
        "lifecycle",
    )
)


def test_no_production_module_imports_the_orphan_state_machine() -> None:
    """Its only importers are itself, its own test, and one benchmark."""

    offenders: dict[str, set[str]] = {}
    for path, source in _sources():
        module = ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts)
        if module in ORPHAN_MODULES:
            continue
        if module == "alphalab.runtime.__init__":
            # The deprecation hook is the one place allowed to name them, and it
            # does so under TYPE_CHECKING plus a lazy importlib call.
            continue
        imported = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        hit = imported & ORPHAN_MODULES
        if hit:
            offenders[module] = hit

    assert offenders == {}, f"the canonical path reaches the orphan runtime: {offenders}"


# --------------------------------------------------------------------------- #
# G. No cross-version read, and the refusal says why
# --------------------------------------------------------------------------- #


def _payload() -> dict[str, Any]:
    """A real captured run, as JSON, to strip v2.13's absences out of."""

    from alphalab.persistence import deserialize, serialize
    from alphalab.runtime.run_snapshot import capture
    from tests.integration.harness import ScriptedStrategy, running_strategy_state

    config = RunConfig(pipeline=_pipeline_config(), mode=ExecutionMode.BACKTEST, seed=7)
    state = RunEngine.initialize(
        config, running_strategy_state("BOUNDARY", ScriptedStrategy("BOUNDARY", "A", {}))
    )
    return dict(deserialize(serialize(capture(state))))


#: What a v2.13 ``SessionSnapshot`` payload did not carry. All three are read by
#: ``finalize``, and all three have a dataclass default -- which is exactly why
#: they cannot be filled in: ``require``'s rule is that a missing field is never
#: given one. This is the portfolio precedent, not the OMS one.
_SESSION_ABSENCES = ("years_elapsed", "risk_free_rate", "compile_analytics")


@pytest.mark.parametrize("absent", _SESSION_ABSENCES)
def test_a_v213_session_payload_is_refused(absent: str) -> None:
    from alphalab.runtime.run_snapshot import from_primitives

    payload = _payload()
    for name in _SESSION_ABSENCES:
        payload.pop(name)

    with pytest.raises(StateDecodeError) as error:
        from_primitives(payload)

    assert any(name in str(error.value) for name in _SESSION_ABSENCES)
    assert absent in _SESSION_ABSENCES


def test_a_v213_backtest_payload_is_refused() -> None:
    """It lacks ``mode``, which is genuinely unknowable: before v2.14 a backtest
    run and a replay run wrote identical payloads, so neither label is honest."""

    from alphalab.runtime.run_snapshot import from_primitives

    payload = _payload()
    for name in (
        "mode",
        "ordering",
        "max_market_data_age_seconds",
        "last_record_timestamp",
        "source_id",
        "skipped",
    ):
        payload.pop(name)

    with pytest.raises(StateDecodeError) as error:
        from_primitives(payload)

    assert "mode" in str(error.value)


def test_a_current_payload_still_reads() -> None:
    """The refusals above are about absence, not about a stricter decoder."""

    from alphalab.runtime.run_snapshot import from_primitives

    assert from_primitives(_payload()).schema_version == RUN_SNAPSHOT_SCHEMA


# --------------------------------------------------------------------------- #
# H. Field coverage, in the shape the other snapshots already use
# --------------------------------------------------------------------------- #


def test_the_run_snapshot_covers_every_run_state_field() -> None:
    carried = {f.name for f in dataclasses.fields(RunSnapshot)} | {
        "config",  # flattened to its own fields, minus pipeline (carried nested)
    }
    missing = {f.name for f in dataclasses.fields(RunState)} - carried

    assert not missing, (
        f"RunState fields absent from RunSnapshot: {sorted(missing)}. Add them to "
        "capture/restore/from_primitives, or state here why they are deliberately "
        "not persisted."
    )


def test_the_run_snapshot_covers_every_run_config_field() -> None:
    carried = {f.name for f in dataclasses.fields(RunSnapshot)}
    missing = (
        {f.name for f in dataclasses.fields(RunConfig)}
        - carried
        - {
            "pipeline",  # carried once, inside the nested pipeline snapshot
            "fill_policy",  # recorded as fill_policy_type and supplied back (ADR-0014)
        }
    )

    assert not missing, f"RunConfig fields absent from RunSnapshot: {sorted(missing)}"


def test_the_run_snapshot_invents_nothing() -> None:
    derived = {"fill_policy_type", "schema_version", "pipeline"}
    state = {f.name for f in dataclasses.fields(RunState)}
    config = {f.name for f in dataclasses.fields(RunConfig)}
    unexpected = {f.name for f in dataclasses.fields(RunSnapshot)} - state - config - derived

    assert not unexpected, f"RunSnapshot invents: {sorted(unexpected)}"


def test_the_step_decoder_reads_every_step_field() -> None:
    """A new ``RunStep`` field reaches a payload automatically and is silently
    dropped on the way back unless ``_step`` is taught to read it."""

    from alphalab.runtime.run_snapshot import _step

    source = inspect.getsource(_step)
    missing = [f.name for f in dataclasses.fields(RunStep) if f'"{f.name}"' not in source]

    assert not missing, f"RunStep fields the decoder does not read: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# I. Round-trip certification at both ends of a run
# --------------------------------------------------------------------------- #


def _objects(config: RunConfig, strategy: StrategyProtocol) -> RunObjects:
    from alphalab.runtime.snapshot import RuntimeObjects

    return RunObjects(
        pipeline=RuntimeObjects(
            config.pipeline.sizing_model,
            config.pipeline.simulator,
            {STRATEGY: strategy},
            config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )


def _round_trip(state: RunState, objects: RunObjects) -> RunState:
    from alphalab.persistence import deserialize, serialize
    from alphalab.runtime.run_snapshot import capture, from_primitives, restore

    return restore(from_primitives(deserialize(serialize(capture(state)))), objects)


STRATEGY = "BOUNDARY"
ASSET = "3d4f5a6b-7c8d-49e0-b1a2-c3d4e5f60718"
SEED = 31337


def _run(
    *,
    mode: ExecutionMode = ExecutionMode.BACKTEST,
    records: int = 4,
    advance_count: int | None = None,
    **config_kwargs: Any,
) -> tuple[RunState, RunObjects]:
    """A deterministic run, advanced ``advance_count`` of ``records`` records."""

    from dataclasses import replace as _replace
    from decimal import Decimal

    from alphalab.common.ids import id_scope
    from alphalab.market.record import MarketRecord
    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        context_factory,
        quote,
        running_strategy_state,
    )

    plan = {2.0 + i: (Decimal("1") if i % 2 == 0 else Decimal("-1")) for i in range(records)}
    config = _replace(backtest_config(STRATEGY, seed=SEED), mode=mode, **config_kwargs)
    strategy = ScriptedStrategy(STRATEGY, ASSET, plan)
    stream = [
        MarketRecord(f"REC-{i}", 2.0 + i, quote(ASSET, 2.0 + i, Decimal("100") + Decimal(i)))
        for i in range(records)
    ]

    with id_scope(SEED):
        state = RunEngine.initialize(config, running_strategy_state(STRATEGY, strategy))
        for record in stream[: records if advance_count is None else advance_count]:
            state, _ = RunEngine.advance(state, record, context_factory)
    return state, _objects(config, strategy)


def test_an_empty_run_round_trips() -> None:
    """Captured immediately after ``initialize``: nothing processed, and it says so."""

    state, objects = _run(advance_count=0)

    assert (state.processed, len(state.steps), len(state.skipped)) == (0, 0, 0)
    assert state.last_record_timestamp is None
    assert _round_trip(state, objects) == state


def test_a_fully_consumed_run_round_trips() -> None:
    state, objects = _run()

    assert state.processed == 4
    assert len(state.steps) == 4
    assert _round_trip(state, objects) == state


@pytest.mark.parametrize(
    "field",
    ["processed", "current_timestamp", "last_record_timestamp", "source_id", "steps", "skipped"],
)
def test_every_bookkeeping_field_survives_the_round_trip(field: str) -> None:
    from dataclasses import replace as _replace

    state, objects = _run()
    state = _replace(state, source_id="DS-BOUNDARY")

    assert getattr(_round_trip(state, objects), field) == getattr(state, field)


def test_the_round_trip_covers_every_bookkeeping_field() -> None:
    """A field added to ``RunState`` joins the parametrize above, or fails here."""

    named = set(
        test_every_bookkeeping_field_survives_the_round_trip.pytestmark[0].args[1]  # type: ignore[attr-defined]
    )
    assert named == {f.name for f in dataclasses.fields(RunState)} - {"config", "pipeline"}


def test_the_run_config_survives_the_round_trip_field_by_field() -> None:
    state, objects = _run(
        mode=ExecutionMode.PAPER, max_market_data_age_seconds=30.0, years_elapsed=2.5
    )
    restored = _round_trip(state, objects)

    for field in dataclasses.fields(RunConfig):
        if field.name == "fill_policy":
            assert type(restored.config.fill_policy) is type(state.config.fill_policy)
            continue
        assert getattr(restored.config, field.name) == getattr(state.config, field.name)


def test_skipped_records_survive_exactly() -> None:
    """A stale record is recorded, not acted on, and the record itself comes back."""

    from decimal import Decimal

    from alphalab.market.record import MarketRecord
    from tests.integration.harness import context_factory, quote

    state, objects = _run(mode=ExecutionMode.PAPER, max_market_data_age_seconds=1.0)
    stale = MarketRecord("STALE-1", 2.0, quote(ASSET, 2.0, Decimal("100")))
    state, result = RunEngine.advance(state, stale, context_factory, now=1_000.0)

    assert result is None, "a stale record produces no pipeline result"
    assert len(state.skipped) == 1
    entry = state.skipped.to_tuple()[0]
    assert entry.record == stale
    assert "older than the 1.0s limit" in entry.reason

    restored = _round_trip(state, objects)
    assert restored.skipped.to_tuple() == state.skipped.to_tuple()
    assert restored == state


def test_the_ordering_cursor_survives_and_still_refuses_after_restore() -> None:
    """``last_record_timestamp`` is the gate's state, so a restored run keeps the gate."""

    from decimal import Decimal

    from alphalab.market.exceptions import MarketValidationError
    from alphalab.market.record import MarketRecord
    from tests.integration.harness import context_factory, quote

    state, objects = _run()
    restored = _round_trip(state, objects)

    assert restored.last_record_timestamp == state.last_record_timestamp == 5.0
    assert restored.current_timestamp == state.current_timestamp == 5.0

    backwards = MarketRecord("OLD-1", 3.0, quote(ASSET, 3.0, Decimal("100")))
    with pytest.raises(MarketValidationError, match="before the last record processed"):
        RunEngine.advance(restored, backwards, context_factory)


def test_a_restored_unordered_run_skips_the_regressing_record_instead() -> None:
    from decimal import Decimal

    from alphalab.market.record import MarketRecord
    from alphalab.market.source import OrderingGuarantee
    from tests.integration.harness import context_factory, quote

    state, objects = _run(ordering=OrderingGuarantee.UNORDERED)
    restored = _round_trip(state, objects)

    backwards = MarketRecord("OLD-1", 3.0, quote(ASSET, 3.0, Decimal("100")))
    after, result = RunEngine.advance(restored, backwards, context_factory)

    assert result is None
    assert len(after.skipped) == 1
    assert after.processed == restored.processed, "a skipped record does not advance the cursor"


# --------------------------------------------------------------------------- #
# J. The envelope holds no clock, and costs no identifiers
# --------------------------------------------------------------------------- #


def test_neither_the_state_nor_the_envelope_holds_a_wall_clock() -> None:
    """The clock is the ``now`` argument to ``advance``, and it is never stored.

    Every timestamp on either type is a *record* timestamp or the funding
    instant. A field holding a reading of the machine's clock would make a
    captured run un-reproducible and would make a restored one disagree with the
    run it continues.
    """

    import inspect as _inspect

    from alphalab.runtime import run, run_snapshot

    clocklike = {"now", "wall_clock", "real_time", "real_current_time", "captured_at", "clock"}
    for cls in (RunState, RunConfig, RunStep, RunSnapshot):
        assert not ({f.name for f in dataclasses.fields(cls)} & clocklike)

    # And nothing on the run path reads one.
    for module in (run, run_snapshot):
        source = _inspect.getsource(module)
        for forbidden in ("time.time(", "time.monotonic(", "perf_counter(", "datetime.now("):
            assert forbidden not in source, f"{module.__name__} reads a wall clock"

    # `now` exists, as a parameter with no stored counterpart.
    assert "now" in _inspect.signature(RunEngine.advance).parameters


def test_capture_and_restore_draw_no_identifiers() -> None:
    """Observing a run must not change it -- ADR-0029 decision 7, for the envelope.

    The store already promises zero draws. So must the projection either side of
    it: a capture that minted an id would consume the run's own next identifier
    and a restored run would then diverge from an uninterrupted one.
    """

    from alphalab.common.ids import current_id_position, id_scope
    from alphalab.persistence import deserialize, serialize
    from alphalab.runtime.run_snapshot import capture, from_primitives, restore

    state, objects = _run()

    with id_scope(SEED):
        before = current_id_position().draws
        payload = serialize(capture(state))
        after_capture = current_id_position().draws
        restore(from_primitives(deserialize(payload)), objects)
        after_restore = current_id_position().draws

    assert after_capture - before == 0, "capture drew from the run's stream"
    assert after_restore - after_capture == 0, "restore drew from the run's stream"


def test_a_run_state_carries_no_run_identity() -> None:
    """Run identity is the caller's, supplied at ``put`` -- ADR-0029 decision 3."""

    for cls in (RunState, RunConfig, RunSnapshot):
        assert "run_id" not in {f.name for f in dataclasses.fields(cls)}


# --------------------------------------------------------------------------- #
# K. Driver parity -- one RunState, three drivers
# --------------------------------------------------------------------------- #


def test_each_driver_declares_its_own_mode_on_the_run_it_produces() -> None:
    """Until v2.14 only a session carried a mode, so a captured backtest and a
    captured replay were indistinguishable."""

    from decimal import Decimal

    from alphalab.market.source import SequenceSource
    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        context_factory,
        dataset_of_quotes,
        running_strategy_state,
    )

    mids = [Decimal("100"), Decimal("101"), Decimal("102")]
    plan = {2.0: Decimal("5")}
    config = backtest_config(STRATEGY, seed=SEED)

    def strategy() -> StrategyRuntimeState:
        return running_strategy_state(STRATEGY, ScriptedStrategy(STRATEGY, ASSET, plan))

    dataset = dataset_of_quotes(ASSET, mids)

    backtest = BacktestEngine.run(config, dataset, strategy(), context_factory)
    replay = ReplayBacktest.run(config, dataset, strategy(), context_factory)
    session = TradingSession.run(
        dataclasses.replace(config, mode=ExecutionMode.PAPER),
        SequenceSource.from_records(dataset.dataset_id, dataset.records),
        strategy(),
        context_factory,
    )

    assert backtest.run.config.mode is ExecutionMode.BACKTEST
    assert replay.backtest.run.config.mode is ExecutionMode.REPLAY
    assert session.config.mode is ExecutionMode.PAPER


def test_each_driver_records_the_input_it_read() -> None:
    from decimal import Decimal

    from alphalab.market.source import SequenceSource
    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        context_factory,
        dataset_of_quotes,
        running_strategy_state,
    )

    config = backtest_config(STRATEGY, seed=SEED)
    dataset = dataset_of_quotes(ASSET, [Decimal("100"), Decimal("101")])

    def strategy() -> StrategyRuntimeState:
        return running_strategy_state(
            STRATEGY, ScriptedStrategy(STRATEGY, ASSET, {2.0: Decimal("5")})
        )

    backtest = BacktestEngine.run(config, dataset, strategy(), context_factory)
    replay = ReplayBacktest.run(config, dataset, strategy(), context_factory)
    session = TradingSession.run(
        dataclasses.replace(config, mode=ExecutionMode.PAPER),
        SequenceSource.from_records("LIVE-1", dataset.records),
        strategy(),
        context_factory,
    )

    assert backtest.run.source_id == dataset.dataset_id
    assert backtest.dataset_id == backtest.run.source_id
    assert replay.backtest.run.source_id == dataset.dataset_id
    assert replay.dataset_id == dataset.dataset_id
    assert session.source_id == "LIVE-1"


def test_a_session_records_a_run_step_per_record() -> None:
    """The one behaviour v2.14 added to sessions: they log what a record produced.

    A backtest always did. One ``RunState`` means one answer, so a session does
    too -- and the step survives capture like every other run field.
    """

    state, objects = _run(mode=ExecutionMode.PAPER)

    assert len(state.steps) == state.processed == 4
    assert [step.index for step in state.steps] == [0, 1, 2, 3]
    assert [step.event_id for step in state.steps] == [f"REC-{i}" for i in range(4)]
    assert _round_trip(state, objects).steps.to_tuple() == state.steps.to_tuple()


def test_replay_keeps_its_cursor_out_of_the_run_state() -> None:
    """Replay is a driver: its cursor is its own, and the run state never sees it."""

    from decimal import Decimal

    from alphalab.backtesting.replay import REPLAY_CURSOR_SEED_OFFSET
    from alphalab.replay.state import ReplayState
    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        context_factory,
        dataset_of_quotes,
        running_strategy_state,
    )

    dataset = dataset_of_quotes(ASSET, [Decimal("100"), Decimal("101")])
    result = ReplayBacktest.run(
        backtest_config(STRATEGY, seed=SEED),
        dataset,
        running_strategy_state(STRATEGY, ScriptedStrategy(STRATEGY, ASSET, {2.0: Decimal("5")})),
        context_factory,
    )

    run_fields = {f.name for f in dataclasses.fields(RunState)}
    cursor_fields = {f.name for f in dataclasses.fields(ReplayState)}
    assert run_fields & cursor_fields == {"current_timestamp"}, (
        "the run cursor and the replay cursor share a name and nothing else"
    )

    # The replay cursor never reaches the run envelope.
    assert not hasattr(result.backtest.run, "session")
    assert "REPLAY_CURSOR_SEED_OFFSET" not in {f.name for f in dataclasses.fields(RunSnapshot)}
    assert REPLAY_CURSOR_SEED_OFFSET != 0, "two streams from one seed"
    assert result.replay_status == "COMPLETED"
    assert result.records_replayed == len(dataset.records)


def test_replay_and_backtest_produce_the_same_run_state_bookkeeping() -> None:
    """Same canonical step, so the run layer agrees on everything but the mode."""

    from decimal import Decimal

    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        context_factory,
        dataset_of_quotes,
        running_strategy_state,
    )

    config = backtest_config(STRATEGY, seed=SEED)
    dataset = dataset_of_quotes(ASSET, [Decimal("100"), Decimal("101"), Decimal("102")])

    def strategy() -> StrategyRuntimeState:
        return running_strategy_state(
            STRATEGY, ScriptedStrategy(STRATEGY, ASSET, {2.0: Decimal("5")})
        )

    backtest = BacktestEngine.run(config, dataset, strategy(), context_factory)
    replay = ReplayBacktest.run(config, dataset, strategy(), context_factory)

    for name in ("processed", "current_timestamp", "last_record_timestamp", "source_id"):
        assert getattr(backtest.run, name) == getattr(replay.backtest.run, name)
    assert backtest.run.steps.to_tuple() == replay.backtest.run.steps.to_tuple()
    assert backtest.fills == replay.backtest.fills


# --------------------------------------------------------------------------- #
# L. Public surface -- what a caller can and cannot reach
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("alphalab.runtime", "RunEngine"),
        ("alphalab.runtime", "RunConfig"),
        ("alphalab.runtime", "RunState"),
        ("alphalab.runtime", "RunStep"),
        ("alphalab.runtime", "SkippedRecord"),
        ("alphalab.runtime", "ExecutionMode"),
        ("alphalab.runtime", "TradingSession"),
        ("alphalab.runtime", "ExecutionPipeline"),
        ("alphalab.runtime", "RuntimeValidationError"),
        ("alphalab.runtime.run", "RunEngine"),
        ("alphalab.runtime.run_snapshot", "RUN_SNAPSHOT_SCHEMA"),
        ("alphalab.runtime.run_snapshot", "RunSnapshot"),
        ("alphalab.runtime.run_snapshot", "RunObjects"),
        ("alphalab.runtime.run_snapshot", "capture"),
        ("alphalab.runtime.run_snapshot", "restore"),
        ("alphalab.runtime.run_snapshot", "from_primitives"),
        ("alphalab.runtime.session", "TradingSession"),
        ("alphalab.runtime.session", "ExecutionMode"),
        ("alphalab.backtesting", "BacktestEngine"),
        ("alphalab.backtesting", "BacktestResult"),
        ("alphalab.backtesting", "ReplayBacktest"),
        ("alphalab.backtesting", "RunConfig"),
        ("alphalab.backtesting", "RunState"),
        ("alphalab.backtesting", "RunStep"),
    ],
)
def test_a_supported_public_name_imports_without_warning(module: str, name: str) -> None:
    """The canonical surface, reachable and silent.

    Silence matters as much as reachability: ``alphalab.runtime`` carries a PEP
    562 notice for the retired state machine, and a notice that fired on
    ``ExecutionPipeline`` would teach everyone to filter ``DeprecationWarning``.
    """

    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loaded = importlib.import_module(module)
        assert hasattr(loaded, name), f"{module}.{name} is not reachable"

    offenders = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not offenders, f"{module}.{name} warned: {offenders}"


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("alphalab.runtime", "SessionState"),
        ("alphalab.runtime", "SessionConfig"),
        ("alphalab.runtime", "SessionSnapshot"),
        ("alphalab.runtime", "SESSION_SNAPSHOT_SCHEMA"),
        ("alphalab.runtime.session", "SessionState"),
        ("alphalab.runtime.session", "SessionConfig"),
        ("alphalab.runtime.session", "SkippedRecord"),
        ("alphalab.backtesting", "BacktestState"),
        ("alphalab.backtesting", "BacktestConfig"),
        ("alphalab.backtesting", "BacktestStep"),
        ("alphalab.backtesting", "BacktestSnapshot"),
        ("alphalab.backtesting", "BACKTEST_SNAPSHOT_SCHEMA"),
        ("alphalab.backtesting.state", "BacktestState"),
        ("alphalab.backtesting.state", "BacktestStep"),
        ("alphalab.backtesting.engine", "BacktestConfig"),
    ],
)
def test_a_retired_name_is_gone_rather_than_aliased(module: str, name: str) -> None:
    """Retired, not shimmed.

    A PEP 562 hook could have served any of these with a notice and kept old
    code running. That would be the compatibility bridge ADR-0030 refuses: the
    point is to remove duplicate ownership, not to hide it behind a warning.
    """

    loaded = importlib.import_module(module)

    assert not hasattr(loaded, name), f"{module}.{name} survived"
    with pytest.raises(ImportError):
        exec(f"from {module} import {name}")


def test_the_runtime_package_serves_no_retired_run_state_through_its_hook() -> None:
    """The PEP 562 hook is for the orphan state machine only."""

    import alphalab.runtime

    served = set(alphalab.runtime._DEPRECATED_RUNTIME)
    retired = {
        "SessionState",
        "SessionConfig",
        "BacktestState",
        "BacktestConfig",
        "BacktestStep",
        "SessionSnapshot",
        "BacktestSnapshot",
    }

    assert not (served & retired), f"the hook resurrects {sorted(served & retired)}"


def test_every_name_the_runtime_package_advertises_is_reachable() -> None:
    """``__all__`` must not promise something neither the module nor the hook has."""

    import warnings

    import alphalab.runtime

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        missing = [n for n in alphalab.runtime.__all__ if not hasattr(alphalab.runtime, n)]

    assert not missing, f"advertised but unreachable: {missing}"


def test_a_fresh_interpreter_imports_the_runtime_silently() -> None:
    """Belt and braces, with the module cache empty and warnings fatal."""

    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::DeprecationWarning",
            "-c",
            "from alphalab.runtime import RunEngine, RunState, RunConfig, RunStep\n"
            "from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA\n"
            "from alphalab.backtesting import BacktestEngine, ReplayBacktest\n"
            "print('ok')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# --------------------------------------------------------------------------- #
# M. BacktestResult is a view, and every property reads through
# --------------------------------------------------------------------------- #


def _finished() -> BacktestResult:
    from decimal import Decimal

    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        context_factory,
        dataset_of_quotes,
        running_strategy_state,
    )

    dataset = dataset_of_quotes(ASSET, [Decimal("100"), Decimal("104"), Decimal("102")])
    return BacktestEngine.run(
        backtest_config(STRATEGY, seed=SEED),
        dataset,
        running_strategy_state(
            STRATEGY, ScriptedStrategy(STRATEGY, ASSET, {2.0: Decimal("5"), 4.0: Decimal("-5")})
        ),
        context_factory,
    )


def test_every_result_property_reads_through_to_the_run() -> None:
    """No property may return a value the run does not hold."""

    result = _finished()

    assert result.config is result.run.config
    assert result.state is result.run.pipeline
    assert result.steps == result.run.steps.to_tuple()
    assert result.records_processed == result.run.processed
    assert result.seed == result.run.config.seed
    assert result.dataset_id == result.run.source_id
    assert result.fills == result.run.pipeline.fills.to_tuple()
    assert result.trades == result.run.pipeline.trades.to_tuple()
    assert result.orders == tuple(result.run.pipeline.oms.orders.orders())
    assert result.equity_curve == result.run.pipeline.portfolio_snapshots.to_tuple()
    assert result.unpriced_assets == result.run.unpriced_assets


def test_a_result_cannot_disagree_with_the_run_it_describes() -> None:
    """Rebuild the result around a different run and every view follows it.

    This is what "projection" buys: there is no second copy to fall out of date.
    """

    result = _finished()
    relabelled = BacktestResult(run=dataclasses.replace(result.run, source_id="OTHER-DS"))

    assert relabelled.dataset_id == "OTHER-DS"
    assert result.dataset_id != "OTHER-DS", "the original is untouched"
    assert relabelled.records_processed == result.records_processed


def test_the_result_reports_real_analytics_and_valuation() -> None:
    """``finalize`` compiled a report, and the views over it are populated."""

    from alphalab.backtesting.views import (
        commission_paid,
        equity_values,
        final_cash,
        final_equity,
        performance_report,
        realized_pnl,
        steps_with_fills,
        unrealized_pnl,
    )

    result = _finished()

    assert result.report is not None, "compile_analytics defaults to True"
    assert performance_report(result) is result.report
    assert final_equity(result) == result.valuation.equity
    assert final_cash(result) == result.valuation.cash
    assert realized_pnl(result) == result.valuation.realized_pnl
    assert unrealized_pnl(result) == result.valuation.unrealized_pnl
    assert commission_paid(result) == result.valuation.commission_paid
    assert equity_values(result) == tuple(s.total_equity for s in result.equity_curve)
    assert steps_with_fills(result) == tuple(s for s in result.steps if s.fills)
    assert steps_with_fills(result), "the run traded, so some steps have fills"


def test_finalize_compiles_only_when_the_run_asked_for_it() -> None:
    from decimal import Decimal

    from alphalab.backtesting.engine import finalize
    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        context_factory,
        dataset_of_quotes,
        running_strategy_state,
    )

    dataset = dataset_of_quotes(ASSET, [Decimal("100"), Decimal("101")])
    off = BacktestEngine.run(
        backtest_config(STRATEGY, seed=SEED, compile_analytics=False),
        dataset,
        running_strategy_state(STRATEGY, ScriptedStrategy(STRATEGY, ASSET, {2.0: Decimal("5")})),
        context_factory,
    )

    assert off.report is None
    assert _finished().report is not None

    # And finalize takes no dataset identity: it reads the one the run carries.
    assert "dataset_id" not in inspect.signature(finalize).parameters
    assert list(inspect.signature(finalize).parameters) == ["state"]


def test_the_result_holds_no_mutable_runtime_state() -> None:
    """A frozen one-field view cannot become a hidden owner."""

    assert BacktestResult.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert ReplayResult.__dataclass_params__.frozen  # type: ignore[attr-defined]

    result = _finished()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.run = result.run  # type: ignore[misc]
