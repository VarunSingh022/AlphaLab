"""Every payload v3.13.0 wrote is read by this build: ``tests/fixtures/snapshots/v3.13.0``.

The fixtures were produced by the v3.13.0 tag's own code (see the README
there). v4.0 moved two subsystems -- pipeline 7 -> 8 and run 4 -> 5, to record
how the sizing model, the simulator and the fill policy were configured as well
as their types (ledger PER-008) -- and left every other where it was, the
checkpoint envelope included. A v3.13.0 payload recorded the types alone and is
upgraded to say exactly that: ``None``, not recorded; its objects are then
checked by type alone, as the release that wrote it checked them.

The oracle is the one the earlier releases' payloads are held to: an unchanged
subsystem re-captures exactly what it read; a moved one is upgraded to what the
payload already meant, changing nothing else, restores, and -- for a run --
continues through the canonical execution path. Until these fixtures, v4.0's
upgrade was held only by v3.12.0's payloads and by version-7 payloads made by
hand; these are what v3.13.0 put on disk.
"""

from __future__ import annotations

import copy
import importlib
import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alphalab.persistence import (
    FileRunStateStore,
    RunStateRef,
    StateDecodeError,
    deserialize,
    serialize,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "v3.13.0"
ASSET = str(uuid.UUID(int=0x3110_0001))


def _load(name: str) -> dict[str, Any]:
    decoded = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)
    return decoded


def _upgraded_run(payload: dict[str, Any]) -> dict[str, Any]:
    """A version-4 run payload as version 5 reads it, by hand: nothing configured is recorded."""

    expected = copy.deepcopy(payload)
    expected["schema_version"] = 5
    expected["fill_policy_description"] = None
    expected["pipeline"]["schema_version"] = 8
    expected["pipeline"]["config"]["sizing_model_description"] = None
    expected["pipeline"]["config"]["simulator_description"] = None
    return expected


@pytest.mark.parametrize(
    ("fixture", "module"),
    [
        ("portfolio.json", "alphalab.portfolio.snapshot"),
        ("oms.json", "alphalab.oms.snapshot"),
        ("allocation.json", "alphalab.allocation.snapshot"),
        ("allocation_eur.json", "alphalab.allocation.snapshot"),
        ("instrument.json", "alphalab.instrument.snapshot"),
        ("lifecycle.json", "alphalab.lifecycle.snapshot"),
        ("fx_feed.json", "alphalab.portfolio.fx_feed"),
        ("broker.json", "alphalab.broker.snapshot"),
    ],
)
def test_every_unchanged_v3_13_subsystem_reads_and_recaptures_exactly(
    fixture: str, module: str
) -> None:
    payload = _load(fixture)
    snapshot = importlib.import_module(module).from_primitives(payload)

    assert deserialize(serialize(snapshot)) == payload


def test_the_upgrade_records_nothing_configured_and_changes_nothing_else() -> None:
    from alphalab.runtime.run_snapshot import from_primitives

    payload = _load("run_backtest.json")
    assert (payload["schema_version"], payload["pipeline"]["schema_version"]) == (4, 7)
    original = copy.deepcopy(payload)
    snapshot = from_primitives(payload)
    assert payload == original, "an upgrade must not modify the payload it reads"

    assert snapshot.fill_policy_description is None
    assert snapshot.pipeline.config.sizing_model_description is None
    assert snapshot.pipeline.config.simulator_description is None
    assert deserialize(serialize(snapshot)) == _upgraded_run(payload)


def _run_objects(
    strategy_id: str, plan: dict[float, Decimal], simulator: Any = None, sizing: Any = None
) -> Any:
    from alphalab.allocation.sizing import FixedQuantitySizing
    from alphalab.execution.policy import ImmediateFill
    from alphalab.execution.simulator import ExecutionSimulator
    from alphalab.runtime.run_snapshot import RunObjects
    from alphalab.runtime.snapshot import RuntimeObjects
    from tests.integration.harness import ScriptedStrategy

    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=FixedQuantitySizing() if sizing is None else sizing,
            simulator=ExecutionSimulator() if simulator is None else simulator,
            strategies={strategy_id: ScriptedStrategy(strategy_id, ASSET, plan)},
        ),
        fill_policy=ImmediateFill(),
    )


def test_a_v3_13_backtest_run_is_upgraded_restored_and_continues() -> None:
    from alphalab.market.record import MarketRecord
    from alphalab.runtime.assumptions import describe
    from alphalab.runtime.run import RunEngine
    from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA, capture, from_primitives, restore
    from alphalab.strategy import StrategyStatus
    from tests.integration.harness import context_factory, sized_quote

    objects = _run_objects("GOLDEN-STRAT", {8.0: Decimal("-1")})
    state = restore(from_primitives(_load("run_backtest.json")), objects)
    assert state.processed == 6
    assert state.pipeline.strategy.strategies["GOLDEN-STRAT"].status is StrategyStatus.RUNNING

    quote = sized_quote(ASSET, 8.0, Decimal("100"), Decimal("100"))
    continued, _ = RunEngine.advance(
        state, MarketRecord("DS-6", quote.timestamp, quote), context_factory
    )
    assert continued.processed == 7
    recaptured = deserialize(serialize(capture(continued)))
    assert recaptured["schema_version"] == RUN_SNAPSHOT_SCHEMA == 5
    assert recaptured["pipeline"]["schema_version"] == 8
    # From the first capture this build makes, the run states how its objects
    # are configured: those it was restored with, which matched by type.
    assert recaptured["pipeline"]["config"]["simulator_description"] == describe(
        objects.pipeline.simulator
    )
    assert recaptured["fill_policy_description"] == describe(objects.fill_policy)


def test_a_v3_13_payload_is_checked_by_type_alone_as_v3_13_checked_it() -> None:
    """What a v3.13.0 payload never recorded cannot be compared; what it recorded still is."""

    from alphalab.allocation.sizing import EqualWeightSizing
    from alphalab.execution.commission import PerShareCommission
    from alphalab.execution.simulator import ExecutionSimulator
    from alphalab.runtime.run_snapshot import from_primitives, restore

    charging = ExecutionSimulator(commission_model=PerShareCommission(Decimal("5.00")))
    state = restore(
        from_primitives(_load("run_backtest.json")),
        _run_objects("GOLDEN-STRAT", {}, simulator=charging),
    )
    assert state.processed == 6

    with pytest.raises(StateDecodeError, match="sizing"):
        restore(
            from_primitives(_load("run_backtest.json")),
            _run_objects("GOLDEN-STRAT", {}, sizing=EqualWeightSizing(num_assets=1)),
        )


def test_a_v3_13_run_with_trade_prints_is_upgraded_and_continues() -> None:
    from alphalab.data.feed import TradeAggressor
    from alphalab.market.record import MarketRecord
    from alphalab.market.tick import Tick
    from alphalab.runtime.run import RunEngine
    from alphalab.runtime.run_snapshot import capture, from_primitives, restore
    from tests.integration.harness import context_factory

    payload = _load("run_ticks.json")
    snapshot = from_primitives(payload)
    assert deserialize(serialize(snapshot)) == _upgraded_run(payload)

    state = restore(snapshot, _run_objects("TICK-STRAT", {}))
    flagged = Tick(
        ASSET, 9.0, Decimal("104"), Decimal("3"), "P-9", "X", "USD", TradeAggressor.BUYER
    )
    continued, _ = RunEngine.advance(state, MarketRecord("T-9", 9.0, flagged), context_factory)
    recaptured = deserialize(serialize(capture(continued)))
    assert recaptured["pipeline"]["market"]["latest_ticks"][ASSET]["aggressor"] == "BUYER"


def test_a_v3_13_live_envelope_is_upgraded_through_its_run() -> None:
    from alphalab.runtime.live_snapshot import from_primitives

    payload = _load("live.json")
    snapshot = from_primitives(payload)

    assert snapshot.schema_version == 2
    assert snapshot.run.schema_version == 5
    assert snapshot.run.pipeline.schema_version == 8
    assert snapshot.broker.schema_version == 2
    expected = copy.deepcopy(payload)
    expected["run"] = _upgraded_run(payload["run"])
    assert deserialize(serialize(snapshot)) == expected


def test_the_v3_13_run_store_file_is_read_and_its_payload_upgraded() -> None:
    from alphalab.runtime.run_snapshot import from_primitives

    store = FileRunStateStore(FIXTURES / "run_store")
    payload = store.get(RunStateRef("golden-run", 1))

    assert payload == (FIXTURES / "run_backtest.json").read_text(encoding="utf-8").rstrip("\n")
    assert from_primitives(deserialize(payload)).processed == 6


def test_a_checkpoint_chain_v3_13_wrote_reads_back_as_its_full_capture() -> None:
    """Checkpoint schema 2 did not move; the run inside each link is upgraded once read."""

    from alphalab.common.serialization import to_serializable
    from alphalab.runtime.checkpoint import read_checkpoints
    from alphalab.runtime.run_snapshot import from_primitives

    folder = FIXTURES / "checkpoints"
    chain = [(folder / f"chain_{index}.json").read_text().rstrip("\n") for index in range(3)]
    assert {deserialize(payload)["schema_version"] for payload in chain} == {2}
    full = deserialize((folder / "full_47.json").read_text())

    from_chain = read_checkpoints(chain)
    assert to_serializable(from_chain) == to_serializable(from_primitives(full))
    assert from_chain.processed == 48
    assert from_chain.pipeline.schema_version == 8, "upgraded through pipeline 7 -> 8"
    assert from_chain.pipeline.config.simulator_description is None, "v3.13 recorded the type"
    assert from_chain.fill_policy_description is None


def test_version_8_is_not_a_second_dialect_of_version_7() -> None:
    """A version-8 payload states the descriptions; one that omits them is refused.

    The ``None`` is supplied by the schema step, which runs only on a payload
    that says it is version 7. A version-8 payload without the fields is one no
    release wrote, and the decoder names what it expected rather than guessing.
    """

    from alphalab.runtime.run_snapshot import from_primitives

    payload = _load("run_backtest.json")
    payload["schema_version"] = 5
    payload["fill_policy_description"] = None
    payload["pipeline"]["schema_version"] = 8
    with pytest.raises(StateDecodeError, match="sizing_model_description"):
        from_primitives(payload)
