"""A restored run continues with live objects configured as the captured run's were (PER-008).

Until v4.0 a snapshot recorded the sizing model, the simulator and the fill
policy by type name alone, and :func:`~alphalab.runtime.run_snapshot.restore`
checked only the type. An ``ExecutionSimulator`` charging $5.00 a share was
accepted in place of the one charging $0.01 that the run was captured with: the
continued run booked its remaining fills at the new rate, and the result's
``execution_assumptions`` -- derived from the configuration -- then described
the whole run by the new rate, including fills the captured part had paid at
the old one. ADR-0029 had listed "live-object parameter persistence" as a
non-goal, and nothing had recorded that gap since.

Each object is now recorded with its
:func:`~alphalab.runtime.assumptions.describe` -- its parameters written out,
no memory address -- and ``restore`` refuses an object whose description
differs. A payload written before v4.0 recorded the type alone; it is upgraded
to say "not recorded" and is checked by type alone, as it always was
(``test_schema_upgrades*.py``).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.allocation.sizing import EqualWeightSizing, FixedQuantitySizing
from alphalab.backtesting import BacktestEngine, LiquidityCappedFill
from alphalab.backtesting.state import BacktestResult
from alphalab.common.ids import id_scope
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.policy import ImmediateFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.lifecycle import digest_run
from alphalab.persistence import StateDecodeError, deserialize, serialize
from alphalab.runtime.assumptions import describe
from alphalab.runtime.run import RunConfig, RunState
from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

STRATEGY = "PER008-STRAT"
ASSET = "00000000-0000-0000-0000-00000000p008".replace("p", "a")
MIDS = [Decimal(100 + index) for index in range(8)]
#: Buy at the second quote, sell at the sixth: one fill each side of the capture.
PLAN = {3.0: Decimal("10"), 7.0: Decimal("-10")}
CAPTURE_AFTER = 4

CENT = ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.01")))
FIVE_DOLLARS = ExecutionSimulator(commission_model=PerShareCommission(Decimal("5.00")))


def _config(simulator: ExecutionSimulator = CENT, **changes: object) -> RunConfig:
    config = backtest_config(STRATEGY, simulator=simulator)
    return replace(config, **changes) if changes else config  # type: ignore[arg-type]


def _strategy() -> ScriptedStrategy:
    return ScriptedStrategy(STRATEGY, ASSET, PLAN)


def _captured(config: RunConfig) -> str:
    """The run's payload after ``CAPTURE_AFTER`` records."""

    dataset = dataset_of_quotes(ASSET, MIDS)
    assert config.seed is not None
    with id_scope(config.seed):
        state = BacktestEngine.initialize(config, running_strategy_state(STRATEGY, _strategy()))
        for record in dataset.records[:CAPTURE_AFTER]:
            state, _ = BacktestEngine.advance(state, record, context_factory)
    return serialize(capture(state))


def _objects(config: RunConfig, **changes: object) -> RunObjects:
    pipeline = RuntimeObjects(
        sizing_model=changes.get("sizing_model", config.pipeline.sizing_model),
        simulator=changes.get("simulator", config.pipeline.simulator),
        strategies={STRATEGY: _strategy()},
        instruments=config.pipeline.instruments,
    )
    return RunObjects(pipeline=pipeline, fill_policy=changes.get("fill_policy", config.fill_policy))  # type: ignore[arg-type]


def _finish(state: RunState) -> BacktestResult:
    dataset = dataset_of_quotes(ASSET, MIDS)
    with BacktestEngine.resume(state):
        for record in dataset.records[CAPTURE_AFTER:]:
            state, _ = BacktestEngine.advance(state, record, context_factory)
    return BacktestEngine.finalize(state)


def test_a_simulator_charging_another_commission_is_refused_naming_both() -> None:
    config = _config()
    payload = _captured(config)

    with pytest.raises(StateDecodeError) as refused:
        restore(from_primitives(deserialize(payload)), _objects(config, simulator=FIVE_DOLLARS))

    message = str(refused.value)
    assert "rate_per_share=5.00" in message, "the object supplied"
    assert "rate_per_share=0.01" in message, "and the one the run was captured with"


def test_a_sizing_model_configured_otherwise_is_refused() -> None:
    config = _config()
    config = replace(config, pipeline=replace(config.pipeline, sizing_model=EqualWeightSizing(3)))
    payload = _captured(config)

    with pytest.raises(StateDecodeError, match="sizing model"):
        restore(
            from_primitives(deserialize(payload)),
            _objects(config, sizing_model=EqualWeightSizing(30)),
        )


def test_a_fill_policy_configured_otherwise_is_refused() -> None:
    config = _config(fill_policy=LiquidityCappedFill(participation_rate=Decimal("0.5")))
    payload = _captured(config)

    with pytest.raises(StateDecodeError, match="fill policy"):
        restore(
            from_primitives(deserialize(payload)),
            _objects(config, fill_policy=LiquidityCappedFill(participation_rate=Decimal("0.25"))),
        )


def test_an_object_of_another_type_is_still_refused_by_its_type() -> None:
    config = _config()
    payload = _captured(config)

    with pytest.raises(StateDecodeError, match="is a EqualWeightSizing, but the snapshot recorded"):
        restore(
            from_primitives(deserialize(payload)),
            _objects(config, sizing_model=EqualWeightSizing(3)),
        )


def test_a_fresh_object_configured_alike_continues_the_run_exactly() -> None:
    """Equality is by configuration, not identity: a fresh process builds new objects."""

    config = _config()
    payload = _captured(config)
    fresh = ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.01")))
    assert fresh is not CENT

    continued = _finish(
        restore(
            from_primitives(deserialize(payload)),
            _objects(
                config,
                simulator=fresh,
                sizing_model=FixedQuantitySizing(),
                fill_policy=ImmediateFill(),
            ),
        )
    )
    assert config.seed is not None
    with id_scope(config.seed):
        state = BacktestEngine.initialize(config, running_strategy_state(STRATEGY, _strategy()))
        for record in dataset_of_quotes(ASSET, MIDS).records:
            state, _ = BacktestEngine.advance(state, record, context_factory)
    uninterrupted = BacktestEngine.finalize(state)

    assert [fill.commission for fill in continued.fills] == [Decimal("0.10"), Decimal("0.10")]
    assert continued.fills == uninterrupted.fills
    assert continued.valuation == uninterrupted.valuation
    assert continued.execution_assumptions == uninterrupted.execution_assumptions


def test_running_a_backtest_does_not_change_how_its_objects_are_described() -> None:
    """A restore compares a fresh object with the one captured mid-run; the run must not
    have changed the captured one's description, or every honest restore would be refused."""

    simulator = ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.01")))
    sizing = FixedQuantitySizing()
    policy = LiquidityCappedFill(participation_rate=Decimal("0.5"))
    before = (describe(simulator), describe(sizing), describe(policy))
    config = replace(
        _config(simulator, fill_policy=policy),
        pipeline=replace(_config(simulator).pipeline, sizing_model=sizing),
    )

    BacktestEngine.run(
        config,
        dataset_of_quotes(ASSET, MIDS),
        running_strategy_state(STRATEGY, _strategy()),
        context_factory,
    )

    assert (describe(simulator), describe(sizing), describe(policy)) == before


def test_two_runs_that_differ_only_in_commission_are_two_configurations() -> None:
    """The manifest's configuration identity read the simulator's type alone before v4.0."""

    def run(simulator: ExecutionSimulator) -> BacktestResult:
        return BacktestEngine.run(
            _config(simulator),
            dataset_of_quotes(ASSET, MIDS),
            running_strategy_state(STRATEGY, _strategy()),
            context_factory,
        )

    cent, five = digest_run(run(CENT)), digest_run(run(FIVE_DOLLARS))

    assert cent.configuration_id != five.configuration_id
    assert digest_run(run(CENT)).configuration_id == cent.configuration_id


def test_a_strategy_configuration_reads_back_as_json_reads_it() -> None:
    """LIM-004, kept: ``StrategyRecord.config`` is persisted through JSON (ADR-0025 decision 4).

    A ``Decimal`` reads back as a string and a tuple as a list. Nothing on the
    execution path reads a strategy's configuration -- its parameters are its
    constructor's and its durable state is declared through
    ``StrategyStateProtocol`` -- and the recorded configuration identity is the
    same either way, because both forms serialize alike. This pins all three, so
    that a change to any of them is a decision rather than a drift.
    """

    from alphalab.runtime.snapshot import capture as capture_pipeline
    from alphalab.runtime.snapshot import from_primitives as pipeline_from_primitives
    from alphalab.strategy.runtime import create_runtime, register_strategy
    from alphalab.strategy.supervisor import RuntimeSupervisor

    configured = {"size": Decimal("10"), "windows": (2, 4)}
    runtime = register_strategy(create_runtime(), STRATEGY, _strategy())
    entry, _ = RuntimeSupervisor.configure(runtime.strategies[STRATEGY], configured, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"quotes"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    strategy_state = replace(runtime, strategies={STRATEGY: entry})

    config = _config()
    state = BacktestEngine.initialize(config, strategy_state)
    payload = deserialize(serialize(capture_pipeline(state.pipeline)))
    (record,) = pipeline_from_primitives(payload).strategy

    assert record.config == {"size": "10", "windows": [2, 4]}, "as JSON reads it"
    assert serialize(record.config) == serialize(configured), "and the identity does not move"
