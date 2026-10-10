"""Every payload v3.12.0 wrote is read by this build: ``tests/fixtures/snapshots/v3.12.0``.

The fixtures were produced by the v3.12.0 tag's own code (see the README
there). v3.13 moved one subsystem -- pipeline 6 -> 7, because the strategy
runtime's status enum was renamed from ``LifecycleState`` to ``StrategyStatus``
(ledger API-001) and a plain enum member is persisted with its class name
(ledger PER-004) -- and left every other where it was. v4.0 moved the pipeline
again, 7 -> 8, and the run envelope 4 -> 5, to record how the live objects were
configured (ledger PER-008); a v3.12 payload recorded their types alone, and is
upgraded to say exactly that: ``None``, not recorded.

The oracle is the one ``test_schema_upgrades.py`` and
``test_schema_upgrades_v3_11.py`` hold the earlier payloads to: an unchanged
subsystem re-captures exactly what it read; a moved one is upgraded to what the
payload already meant, restores, and -- for a run -- continues through the
canonical execution path. Here the upgrade is also held to changing nothing but
what it says it changes.
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

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "v3.12.0"
ASSET = str(uuid.UUID(int=0x3110_0001))


def _load(name: str) -> dict[str, Any]:
    decoded = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)
    return decoded


def _renamed(pipeline: dict[str, Any]) -> dict[str, Any]:
    """A version-6 pipeline payload as version 8 reads it: by hand, not by the upgrade.

    Version 7 renamed each status; version 8 says the live objects' configuration
    was not recorded.
    """

    expected = copy.deepcopy(pipeline)
    expected["schema_version"] = 8
    for record in expected["strategy"]:
        assert record["status"].startswith("LifecycleState.")
        record["status"] = "StrategyStatus." + record["status"].split(".", 1)[1]
    expected["config"]["sizing_model_description"] = None
    expected["config"]["simulator_description"] = None
    return expected


def _upgraded_run(payload: dict[str, Any]) -> dict[str, Any]:
    """A version-4 run payload as version 5 reads it, by hand: its fill policy undescribed."""

    return {
        **copy.deepcopy(payload),
        "schema_version": 5,
        "fill_policy_description": None,
        "pipeline": _renamed(payload["pipeline"]),
    }


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
def test_every_unchanged_v3_12_subsystem_reads_and_recaptures_exactly(
    fixture: str, module: str
) -> None:
    payload = _load(fixture)
    snapshot = importlib.import_module(module).from_primitives(payload)

    assert deserialize(serialize(snapshot)) == payload


def test_the_upgrade_renames_each_status_and_changes_nothing_else() -> None:
    from alphalab.runtime.run_snapshot import from_primitives

    payload = _load("run_backtest.json")
    original = copy.deepcopy(payload)
    snapshot = from_primitives(payload)
    assert payload == original, "an upgrade must not modify the payload it reads"

    expected = _upgraded_run(payload)
    assert deserialize(serialize(snapshot)) == expected


def _run_objects(strategy_id: str, plan: dict[float, Decimal]) -> Any:
    from alphalab.allocation.sizing import FixedQuantitySizing
    from alphalab.execution.policy import ImmediateFill
    from alphalab.execution.simulator import ExecutionSimulator
    from alphalab.runtime.run_snapshot import RunObjects
    from alphalab.runtime.snapshot import RuntimeObjects
    from tests.integration.harness import ScriptedStrategy

    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=FixedQuantitySizing(),
            simulator=ExecutionSimulator(),
            strategies={strategy_id: ScriptedStrategy(strategy_id, ASSET, plan)},
        ),
        fill_policy=ImmediateFill(),
    )


def test_a_v3_12_backtest_run_is_upgraded_restored_and_continues() -> None:
    from alphalab.market.record import MarketRecord
    from alphalab.runtime.run import RunEngine
    from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA, capture, from_primitives, restore
    from alphalab.strategy import StrategyStatus
    from tests.integration.harness import context_factory, sized_quote

    state = restore(
        from_primitives(_load("run_backtest.json")),
        _run_objects("GOLDEN-STRAT", {8.0: Decimal("-1")}),
    )
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
    assert [record["status"] for record in recaptured["pipeline"]["strategy"]] == [
        "StrategyStatus.RUNNING"
    ]


def test_a_v3_12_run_with_trade_prints_is_upgraded_and_continues() -> None:
    from alphalab.data.feed import TradeAggressor
    from alphalab.market.record import MarketRecord
    from alphalab.market.tick import Tick
    from alphalab.runtime.run import RunEngine
    from alphalab.runtime.run_snapshot import capture, from_primitives, restore
    from tests.integration.harness import context_factory

    payload = _load("run_ticks.json")
    snapshot = from_primitives(payload)
    expected = _upgraded_run(payload)
    assert deserialize(serialize(snapshot)) == expected

    state = restore(snapshot, _run_objects("TICK-STRAT", {}))
    flagged = Tick(
        ASSET, 9.0, Decimal("104"), Decimal("3"), "P-9", "X", "USD", TradeAggressor.BUYER
    )
    continued, _ = RunEngine.advance(state, MarketRecord("T-9", 9.0, flagged), context_factory)
    recaptured = deserialize(serialize(capture(continued)))
    assert recaptured["pipeline"]["market"]["latest_ticks"][ASSET]["aggressor"] == "BUYER"


def test_a_v3_12_live_envelope_is_upgraded_through_both_of_its_halves() -> None:
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


def test_the_v3_12_run_store_file_is_read_and_its_payload_upgraded() -> None:
    from alphalab.runtime.run_snapshot import from_primitives

    store = FileRunStateStore(FIXTURES / "run_store")
    payload = store.get(RunStateRef("golden-run", 1))

    assert payload == (FIXTURES / "run_backtest.json").read_text(encoding="utf-8").rstrip("\n")
    assert from_primitives(deserialize(payload)).processed == 6


def test_the_old_name_is_not_a_second_dialect_of_version_7() -> None:
    """Upgraded is not tolerated: a version-7 payload says ``StrategyStatus`` or is refused.

    The rename is carried by the schema step, which runs only on a payload that
    says it is version 6. A version-7 payload with the old name is one no
    release wrote, and the decoder names what it expected rather than guessing.
    """

    from alphalab.runtime.run_snapshot import from_primitives

    payload = _load("run_backtest.json")
    payload["pipeline"]["schema_version"] = 7
    with pytest.raises(
        StateDecodeError, match=r"is not a StrategyStatus: 'LifecycleState\.RUNNING'"
    ):
        from_primitives(payload)
