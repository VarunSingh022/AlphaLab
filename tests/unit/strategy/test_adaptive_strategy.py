"""An adaptive strategy on the execution path: research/live consistency and restart.

The strategy is driven through the real run -- :class:`TradingSession` over
market records, capture and restore through the run snapshot, a fresh instance
on resume -- and held to three properties: a run advances its state exactly as a
research replay over the same observations does; the run's own record carries
the adaptive state, so its identity commits to what was learned; and a run
stopped anywhere resumes with exactly what it had learned.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting.state import BacktestResult
from alphalab.common.ids import id_scope
from alphalab.execution.policy import ImmediateFill
from alphalab.instrument.registry import InstrumentRegistry
from alphalab.lifecycle.reproducibility import digest_run
from alphalab.market.record import MarketRecord
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.run import ExecutionMode, RunConfig, RunState
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.run_snapshot import from_primitives as run_from_primitives
from alphalab.runtime.run_snapshot import restore as restore_run
from alphalab.runtime.session import TradingSession
from alphalab.runtime.snapshot import RuntimeObjects, StrategyStateRecord
from alphalab.strategy import (
    AdaptationMode,
    AdaptiveConfiguration,
    AdaptiveDecision,
    AdaptiveObservation,
    AdaptiveReplay,
    AdaptiveState,
    AdaptiveStateError,
    AdaptiveStrategy,
    DecisionTiming,
    StrategyProtocol,
    StrategyStateProtocol,
    TrailingZScoreRule,
    UpdateCadence,
    observation_stream,
    replay_updates,
)
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration.harness import (
    context_factory,
    equity,
    pipeline_config,
    registry_of,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = "ADAPTIVE-ZSCORE"
SEED = 370_000
INSTRUMENT = equity("ADX")
ASSET_ID = INSTRUMENT.asset_id
STREAM = f"quotes:{ASSET_ID}"
MIDS = (
    "100", "101", "99", "104", "97", "100", "106", "95", "101", "99",
    "103", "98", "100", "107", "94", "100", "102", "99", "105", "96",
)  # fmt: skip

CONFIGURATION = AdaptiveConfiguration(
    name="zscore",
    rule_id="trailing_zscore",
    rule_version=1,
    inputs=("price",),
    parameters={"window": 4, "entry": 1.0},
    cadence=UpdateCadence.EVERY_OBSERVATION,
    cadence_every=None,
    cadence_seconds=None,
    decision_timing=DecisionTiming.BEFORE_UPDATE,
    warmup=4,
)
RULE = TrailingZScoreRule()


class ZScoreReversion(AdaptiveStrategy):
    """Trades five shares against a z-score stretched beyond the entry."""

    def observation_for(self, event: Any, sequence: int) -> AdaptiveObservation | None:
        quote = getattr(event, "quote", None)
        if quote is None:
            return None
        mid = (quote.bid + quote.ask) / Decimal("2")
        return AdaptiveObservation(float(quote.timestamp), sequence, STREAM, {"price": float(mid)})

    def intents_for(
        self, decision: AdaptiveDecision, event: Any, context: StrategyContext
    ) -> Iterable[Intent]:
        signal = decision.outputs.get("signal")
        if not signal:
            return ()
        return (
            Intent(
                strategy_id=self.strategy_id,
                instrument=ASSET_ID,
                target=Decimal(str(signal)) * Decimal("5"),
                timestamp=event.quote.timestamp,
            ),
        )


def _strategy(
    configuration: AdaptiveConfiguration = CONFIGURATION,
    mode: AdaptationMode = AdaptationMode.LEARNING,
    initial: AdaptiveState | None = None,
) -> ZScoreReversion:
    return ZScoreReversion(STRATEGY_ID, configuration, RULE, mode, initial)


def _records() -> list[MarketRecord]:
    return [
        MarketRecord(
            event_id=f"REC-{index}",
            timestamp=2.0 + index,
            payload=sized_quote(ASSET_ID, 2.0 + index, Decimal(mid), Decimal("1000")),
        )
        for index, mid in enumerate(MIDS)
    ]


def _config() -> RunConfig:
    return RunConfig(
        pipeline=replace(pipeline_config(STRATEGY_ID), instruments=_registry()),
        mode=ExecutionMode.BACKTEST,
        fill_policy=ImmediateFill(),
        seed=SEED,
        start_timestamp=1.0,
    )


def _registry() -> InstrumentRegistry:
    return registry_of(INSTRUMENT)


def _drive(state: RunState, records: list[MarketRecord]) -> RunState:
    for record in records:
        state, _ = TradingSession.advance(state, record, context_factory)
    return state


def _run(strategy: ZScoreReversion, records: list[MarketRecord] | None = None) -> RunState:
    config = _config()
    with id_scope(SEED):
        start = TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
        return _drive(start, records if records is not None else _records())


def _objects(config: RunConfig, strategy: object) -> RunObjects:
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY_ID: strategy},  # type: ignore[dict-item]
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )


def _orders(state: RunState) -> tuple[tuple[float, Decimal], ...]:
    return tuple((order.created_at, order.quantity) for order in state.pipeline.oms.orders.orders())


def _research_replay() -> AdaptiveReplay:
    rows = [(2.0 + index, {"price": float(Decimal(mid))}) for index, mid in enumerate(MIDS)]
    return replay_updates(
        CONFIGURATION,
        RULE,
        observation_stream(STREAM, rows, first_sequence=0),
        AdaptationMode.LEARNING,
    )


# --------------------------------------------------------------------------- #
# The protocol
# --------------------------------------------------------------------------- #


def test_the_strategy_is_dispatchable_and_declares_its_state() -> None:
    strategy = _strategy()

    assert isinstance(strategy, StrategyProtocol)
    assert isinstance(strategy, StrategyStateProtocol)
    assert strategy.strategy_state_version() == 1


# --------------------------------------------------------------------------- #
# Research and live are one computation
# --------------------------------------------------------------------------- #


def test_a_run_advances_the_state_exactly_as_a_research_replay_does() -> None:
    strategy = _strategy()
    finished = _run(strategy)
    research = _research_replay()

    assert strategy.adaptive_state == research.final
    assert strategy.adaptive_state.state_id == research.final.state_id
    signals = [decision.outputs.get("signal") for decision in research.decisions]
    traded = [(2.0 + index) for index, signal in enumerate(signals) if signal]
    assert [created for created, _ in _orders(finished)] == traded
    assert traded, "the workload is meant to trade"


def test_the_run_snapshot_carries_the_adaptive_state() -> None:
    strategy = _strategy()
    snapshot = capture_run(_run(strategy))

    (record,) = snapshot.pipeline.strategy
    assert isinstance(record.state, StrategyStateRecord)
    assert record.state.version == 1
    assert record.state.payload["state_id"] == strategy.adaptive_state.state_id
    assert record.state.payload["lineage"] == strategy.adaptive_state.lineage


def test_the_run_identity_commits_to_what_was_learned() -> None:
    """Same prices, same orders -- a different learning configuration is a different run."""

    baseline = digest_run(BacktestResult(_run(_strategy())))
    again = digest_run(BacktestResult(_run(_strategy())))
    slower = replace(CONFIGURATION, cadence=UpdateCadence.EVERY_N_OBSERVATIONS, cadence_every=1)
    relearned = digest_run(BacktestResult(_run(_strategy(configuration=slower))))

    assert again.result_id == baseline.result_id
    assert relearned.result_id != baseline.result_id


# --------------------------------------------------------------------------- #
# Stop, store, resume into a fresh instance
# --------------------------------------------------------------------------- #


def test_a_stopped_run_resumes_with_exactly_what_it_had_learned() -> None:
    uninterrupted_strategy = _strategy()
    uninterrupted = _run(uninterrupted_strategy)
    records = _records()
    config = _config()

    for boundary in (1, 4, 5, 9, 13, 19):
        with id_scope(SEED):
            partial = _drive(
                TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, _strategy())),
                records[:boundary],
            )
        payload = serialize(capture_run(partial))
        fresh = _strategy()
        restored = restore_run(run_from_primitives(deserialize(payload)), _objects(config, fresh))
        with TradingSession.resume(restored):
            resumed = _drive(restored, records[boundary:])

        assert fresh.adaptive_state == uninterrupted_strategy.adaptive_state, boundary
        assert _orders(resumed) == _orders(uninterrupted), boundary
        assert serialize(capture_run(resumed)) == serialize(capture_run(uninterrupted)), boundary


def test_a_tampered_adaptive_state_refuses_the_whole_restore() -> None:
    config = _config()
    with id_scope(SEED):
        partial = _drive(
            TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, _strategy())),
            _records()[:8],
        )
    primitives = json.loads(serialize(capture_run(partial)))
    state = primitives["pipeline"]["strategy"][0]["state"]["payload"]
    state["payload"]["values"] = [1.0, 2.0, 3.0, 4.0]

    with pytest.raises(StateDecodeError, match="refused"):
        restore_run(run_from_primitives(primitives), _objects(config, _strategy()))


def test_restore_state_refuses_a_version_this_class_did_not_write() -> None:
    strategy = _strategy()

    with pytest.raises(AdaptiveStateError, match="version 7"):
        strategy.restore_state(strategy.capture_state(), 7)


# --------------------------------------------------------------------------- #
# Controlled adaptation
# --------------------------------------------------------------------------- #


def test_a_frozen_strategy_trades_on_a_research_trained_state_and_learns_nothing() -> None:
    trained = _research_replay().final
    strategy = _strategy(mode=AdaptationMode.FROZEN, initial=trained)
    later = [
        MarketRecord(
            event_id=f"LATE-{index}",
            timestamp=100.0 + index,
            payload=sized_quote(ASSET_ID, 100.0 + index, Decimal(mid), Decimal("1000")),
        )
        for index, mid in enumerate(("120", "80", "100"))
    ]

    finished = _run(strategy, later)

    assert strategy.adaptive_state.payload == trained.payload
    assert strategy.adaptive_state.version == trained.version
    assert strategy.adaptive_state.observations == trained.observations + 3
    assert len(_orders(finished)) == 2, "120 and 80 are far outside the trained window"


def test_an_initial_state_from_another_configuration_is_refused() -> None:
    other = replace(CONFIGURATION, name="other")
    trained = _research_replay().final

    with pytest.raises(AdaptiveStateError, match="belongs to"):
        _strategy(configuration=other, initial=trained)
