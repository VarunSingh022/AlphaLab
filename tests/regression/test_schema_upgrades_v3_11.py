"""Every payload v3.11.0 wrote is read by this build: ``tests/fixtures/snapshots/v3.11.0``.

The fixtures were produced by the v3.11.0 tag's own code
(``docs/audit/scripts/generate_v3_11_0_snapshot_fixtures.py``; see the README
there). v3.12 moved four subsystems -- pipeline 5 -> 6, run 3 -> 4, allocation
2 -> 3, instrument 2 -> 3 -- and left the rest where they were.

The oracle is the one ``test_schema_upgrades.py`` holds the v3.9.0 payloads to:
an unchanged subsystem re-captures exactly what it read; a moved one is
upgraded to what the payload already meant, restores, and -- for a run --
continues through the canonical execution path.
"""

from __future__ import annotations

import copy
import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alphalab.persistence import FileRunStateStore, RunStateRef, deserialize, serialize

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "v3.11.0"
ASSET = str(uuid.UUID(int=0x3110_0001))


def _load(name: str) -> dict[str, Any]:
    decoded = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)
    return decoded


@pytest.mark.parametrize(
    ("fixture", "module"),
    [
        ("portfolio.json", "alphalab.portfolio.snapshot"),
        ("oms.json", "alphalab.oms.snapshot"),
        ("lifecycle.json", "alphalab.lifecycle.snapshot"),
        ("fx_feed.json", "alphalab.portfolio.fx_feed"),
        ("broker.json", "alphalab.broker.snapshot"),
    ],
)
def test_every_unchanged_v3_11_subsystem_reads_and_recaptures_exactly(
    fixture: str, module: str
) -> None:
    import importlib

    payload = _load(fixture)
    snapshot = importlib.import_module(module).from_primitives(payload)

    assert deserialize(serialize(snapshot)) == payload


def test_a_v3_11_allocation_is_upgraded_with_no_ceiling_enforced() -> None:
    from alphalab.allocation.snapshot import ALLOCATION_SNAPSHOT_SCHEMA, from_primitives, restore

    payload = _load("allocation.json")
    original = copy.deepcopy(payload)
    snapshot = from_primitives(payload)

    assert payload == original, "an upgrade must not modify the payload it reads"
    assert snapshot.schema_version == ALLOCATION_SNAPSHOT_SCHEMA == 3
    state = restore(snapshot)
    assert not state.budget.enforce_strategy_budgets
    recaptured = deserialize(serialize(snapshot))
    assert recaptured["budget"]["enforce_strategy_budgets"] is False
    assert recaptured["reservations"] == payload["reservations"]


def test_a_v3_11_budget_currency_is_read_now_where_v3_11_dropped_it() -> None:
    """Ledger PER-006: v3.11 wrote the budget's currency and its decoder ignored it."""

    from alphalab.allocation.snapshot import from_primitives, restore

    payload = _load("allocation_eur.json")
    assert payload["budget"]["currency"] == "EUR"

    state = restore(from_primitives(payload))

    assert state.budget.currency == "EUR"


def test_a_v3_11_registry_is_upgraded_with_its_sector_as_a_dimension() -> None:
    from alphalab.instrument.classification import SECTOR
    from alphalab.instrument.snapshot import from_primitives, restore

    payload = _load("instrument.json")
    snapshot = from_primitives(payload)
    registry = restore(snapshot)

    assert snapshot.schema_version == 3
    acme = next(record for record in registry.instruments.values() if record.symbol == "ACME")
    assert registry.label_of(acme.asset_id, SECTOR) == "Technology"
    assert acme.asset_id in registry.bucket_members(SECTOR, "Technology")
    assert acme.dated_aliases, "the v3.11 dated alias is carried"
    assert deserialize(serialize(snapshot))["schema_version"] == 3


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


def test_a_v3_11_backtest_run_is_upgraded_restored_and_continues() -> None:
    from alphalab.market.record import MarketRecord
    from alphalab.runtime.retention import RetentionPolicy
    from alphalab.runtime.run import RunEngine
    from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA, capture, from_primitives, restore
    from tests.integration.harness import context_factory, sized_quote

    payload = _load("run_backtest.json")
    original = copy.deepcopy(payload)
    snapshot = from_primitives(payload)
    assert payload == original

    state = restore(snapshot, _run_objects("GOLDEN-STRAT", {8.0: Decimal("-1")}))
    assert state.processed == 6
    config = state.config.pipeline
    # v3.11 declared none of what v3.12 added, and the upgrade says exactly that.
    assert config.calendars.by_exchange == {}
    assert config.retention == RetentionPolicy()
    assert not config.budget.enforce_strategy_budgets
    assert config.risk_limits.classification == ()
    assert state.last_observation is None and state.observations_delivered == 0

    quote = sized_quote(ASSET, 8.0, Decimal("100"), Decimal("100"))
    continued, _ = RunEngine.advance(
        state, MarketRecord("DS-6", quote.timestamp, quote), context_factory
    )
    assert continued.processed == 7
    recaptured = deserialize(serialize(capture(continued)))
    assert recaptured["schema_version"] == RUN_SNAPSHOT_SCHEMA == 4
    assert recaptured["pipeline"]["schema_version"] == 7
    assert recaptured["pipeline"]["allocation"]["schema_version"] == 3
    assert recaptured["pipeline"]["dropped"] == {} and recaptured["dropped"] == {}


def test_v3_11_prints_are_read_as_unflagged_and_a_run_continues_with_flagged_ones() -> None:
    """Ledger FEA-004: a v3.11 tick recorded no aggressor, so each reads as ``None``."""

    from alphalab.data.feed import TradeAggressor
    from alphalab.market.events import TickReceived, TradeReceived
    from alphalab.market.record import MarketRecord
    from alphalab.market.tick import Tick
    from alphalab.runtime.run import RunEngine
    from alphalab.runtime.run_snapshot import capture, from_primitives, restore
    from tests.integration.harness import context_factory

    payload = _load("run_ticks.json")
    state = restore(from_primitives(payload), _run_objects("TICK-STRAT", {}))

    history_ticks = [
        record.tick
        for record in state.pipeline.market.history
        if isinstance(record, TickReceived | TradeReceived)
    ]
    assert len(history_ticks) == 4
    assert {tick.aggressor for tick in history_ticks} == {None}
    assert {tick.aggressor for tick in state.pipeline.market.latest_ticks.values()} == {None}

    flagged = Tick(
        ASSET, 9.0, Decimal("104"), Decimal("3"), "P-9", "X", "USD", TradeAggressor.SELLER
    )
    continued, _ = RunEngine.advance(state, MarketRecord("T-9", 9.0, flagged), context_factory)
    recaptured = deserialize(serialize(capture(continued)))
    assert recaptured["pipeline"]["market"]["latest_ticks"][ASSET]["aggressor"] == "SELLER"


def test_a_v3_11_live_envelope_is_upgraded_through_both_of_its_halves() -> None:
    from alphalab.runtime.live_snapshot import from_primitives

    snapshot = from_primitives(_load("live.json"))

    assert snapshot.schema_version == 2
    assert snapshot.run.schema_version == 4
    assert snapshot.run.pipeline.schema_version == 7
    assert snapshot.broker.schema_version == 2


def test_the_v3_11_run_store_file_is_read_and_its_payload_upgraded() -> None:
    from alphalab.runtime.run_snapshot import from_primitives

    store = FileRunStateStore(FIXTURES / "run_store")
    payload = store.get(RunStateRef("golden-run", 1))

    assert payload == (FIXTURES / "run_backtest.json").read_text(encoding="utf-8").rstrip("\n")
    assert from_primitives(deserialize(payload)).processed == 6
