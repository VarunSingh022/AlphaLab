"""PER-001: every payload a release wrote is read by this build, or refused honestly.

Until v3.10 each snapshot subsystem read exactly the schema version its build
wrote -- "There is no migration path; read it with the build that wrote it." --
so the first fix needing a new durable field would have made every earlier
payload unreadable. :mod:`alphalab.persistence.upgrade` replaces that rule with
explicit, pure, composable steps, and this file holds them to the payloads the
last release actually wrote: ``tests/fixtures/snapshots/v3.9.0``, produced by the
v3.9.0 tag's own code (see the README there).

The oracle is not "decodes without error". An upgraded payload must restore
into a state that re-captures at the current version, round-trips, and -- for a
run -- continues: the upgraded run takes another record through the canonical
execution path.
"""

from __future__ import annotations

import copy
import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alphalab.common.currency_units import CurrencyUnits
from alphalab.persistence import FileRunStateStore, RunStateRef, deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.upgrade import (
    SchemaHistory,
    SchemaStep,
    SchemaUpgradeRefused,
    SchemaUpgradeWarning,
    declared_version,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "v3.9.0"


def _load(name: str) -> dict[str, Any]:
    decoded = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)
    return decoded


# --------------------------------------------------------------------------- #
# Every v3.9.0 payload
# --------------------------------------------------------------------------- #


def test_a_v3_9_portfolio_is_upgraded_and_keeps_every_amount() -> None:
    from alphalab.portfolio.snapshot import (
        PORTFOLIO_SNAPSHOT_SCHEMA,
        capture,
        from_primitives,
        restore,
    )

    payload = _load("portfolio.json")
    assert payload["schema_version"] == 3
    state = restore(from_primitives(payload))

    # USDT is outside ISO 4217 and v3.9 booked it at a cent: declared, not guessed.
    assert state.account.currency_units == CurrencyUnits({"USDT": 2})
    # Every recorded amount is the same number it was.
    for currency, amount in payload["balances"].items():
        assert state.cash.balance(currency) == Decimal(amount)
    for record in payload["positions"]:
        position = state.positions[record["asset_id"]]
        assert position.basis == Decimal(record["cost_basis"])
        assert position.quantity == Decimal(record["quantity"])
    # Yen is whole yen, and each position knows its currency's unit.
    assert state.cash.balance("JPY") == Decimal("4749850")
    assert state.positions["7203"].minor_units == 0
    assert state.positions["AAPL"].minor_units == 2

    recaptured = deserialize(serialize(capture(state)))
    assert recaptured["schema_version"] == PORTFOLIO_SNAPSHOT_SCHEMA == 4
    assert restore(from_primitives(recaptured)) == state


def test_a_v3_9_portfolio_holding_fractional_yen_is_refused_not_rounded() -> None:
    from alphalab.portfolio.snapshot import from_primitives

    with pytest.raises(SchemaUpgradeRefused, match=r"JPY amounts that are not whole"):
        from_primitives(_load("portfolio_fractional_yen.json"))


@pytest.mark.parametrize(
    ("fixture", "module"),
    [
        ("oms.json", "alphalab.oms.snapshot"),
        ("allocation.json", "alphalab.allocation.snapshot"),
        ("instrument.json", "alphalab.instrument.snapshot"),
        ("lifecycle.json", "alphalab.lifecycle.snapshot"),
        ("fx_feed.json", "alphalab.portfolio.fx_feed"),
        ("broker.json", "alphalab.broker.snapshot"),
    ],
)
def test_every_unchanged_v3_9_subsystem_still_reads(fixture: str, module: str) -> None:
    import importlib

    loaded = importlib.import_module(module)
    payload = _load(fixture)
    snapshot = loaded.from_primitives(payload)

    # Nothing moved in these subsystems, so a re-capture writes what was read.
    assert deserialize(serialize(snapshot)) == payload


def _run_objects(strategy_id: str, asset_id: str) -> Any:
    from alphalab.allocation.sizing import FixedQuantitySizing
    from alphalab.execution.policy import ImmediateFill
    from alphalab.execution.simulator import ExecutionSimulator
    from alphalab.runtime.run_snapshot import RunObjects
    from alphalab.runtime.snapshot import RuntimeObjects
    from tests.integration.harness import ScriptedStrategy

    strategy = ScriptedStrategy(strategy_id, asset_id, {8.0: Decimal("-1")})
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=FixedQuantitySizing(),
            simulator=ExecutionSimulator(),
            strategies={strategy_id: strategy},
        ),
        fill_policy=ImmediateFill(),
    )


def test_a_v3_9_backtest_run_is_upgraded_restored_and_continues() -> None:
    from alphalab.analytics import Periodicity
    from alphalab.runtime.run import RunEngine
    from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA, capture, from_primitives, restore
    from tests.integration.harness import context_factory, sized_quote

    payload = _load("run_backtest.json")
    original = copy.deepcopy(payload)
    asset_id = str(uuid.UUID(int=0x3900_0001))

    # The v3.9 run declared a daily loss limit, which v3.9 never enforced and
    # recorded no trading day for: the upgrade drops it, and says so.
    with pytest.warns(SchemaUpgradeWarning, match=r"daily_loss of 100000000 was not carried"):
        snapshot = from_primitives(payload)
    assert payload == original, "an upgrade must not modify the payload it reads"

    state = restore(snapshot, _run_objects("GOLDEN-STRAT", asset_id))
    assert state.processed == 6
    assert state.config.pipeline.risk_limits.daily_loss is None
    assert state.config.periods_per_year is None  # v3.9 declared none
    assert state.config.years_elapsed == 1.0  # kept exactly as recorded
    report = state.pipeline.analytics.reports[-1]
    assert report.returns.periodicity is Periodicity.ASSUMED
    assert report.returns.periods_per_year == 252.0

    # The upgraded run is a run: it takes the next record through the canonical path.
    continued, _ = RunEngine.advance(
        state, _record(sized_quote(asset_id, 8.0, Decimal("100"), Decimal("100"))), context_factory
    )
    assert continued.processed == 7
    recaptured = deserialize(serialize(capture(continued)))
    assert recaptured["schema_version"] == RUN_SNAPSHOT_SCHEMA == 2
    assert recaptured["pipeline"]["schema_version"] == 4
    assert recaptured["pipeline"]["portfolio"]["schema_version"] == 4


def _record(quote: Any) -> Any:
    from alphalab.market.record import MarketRecord

    return MarketRecord("DS-6", quote.timestamp, quote)


def test_a_v3_9_live_envelope_is_upgraded_through_both_of_its_halves() -> None:
    from alphalab.runtime.live_snapshot import from_primitives

    with pytest.warns(SchemaUpgradeWarning, match="daily_loss"):
        snapshot = from_primitives(_load("live.json"))

    assert snapshot.run.schema_version == 2
    assert snapshot.run.pipeline.schema_version == 4
    assert snapshot.broker.order_bindings


def test_the_v3_9_run_store_file_is_read_and_its_payload_upgraded() -> None:
    from alphalab.runtime.run_snapshot import from_primitives

    store = FileRunStateStore(FIXTURES / "run_store")
    payload = store.get(RunStateRef("golden-run", 1))

    assert payload == (FIXTURES / "run_backtest.json").read_text(encoding="utf-8").rstrip("\n")
    with pytest.warns(SchemaUpgradeWarning, match="daily_loss"):
        assert from_primitives(deserialize(payload)).processed == 6


# --------------------------------------------------------------------------- #
# The mechanism
# --------------------------------------------------------------------------- #


def test_every_snapshot_subsystem_declares_a_history_at_its_current_version() -> None:
    from alphalab.allocation.snapshot import ALLOCATION_SCHEMA_HISTORY, ALLOCATION_SNAPSHOT_SCHEMA
    from alphalab.broker.snapshot import BROKER_SCHEMA_HISTORY, BROKER_SNAPSHOT_SCHEMA
    from alphalab.instrument.snapshot import INSTRUMENT_SCHEMA_HISTORY, INSTRUMENT_SNAPSHOT_SCHEMA
    from alphalab.lifecycle.snapshot import LIFECYCLE_SCHEMA_HISTORY, LIFECYCLE_SNAPSHOT_SCHEMA
    from alphalab.oms.snapshot import OMS_SCHEMA_HISTORY, OMS_SNAPSHOT_SCHEMA
    from alphalab.portfolio.fx_feed import FX_FEED_SCHEMA_HISTORY, FX_FEED_SNAPSHOT_SCHEMA
    from alphalab.portfolio.snapshot import PORTFOLIO_SCHEMA_HISTORY, PORTFOLIO_SNAPSHOT_SCHEMA
    from alphalab.runtime.live_snapshot import LIVE_SCHEMA_HISTORY, LIVE_SNAPSHOT_SCHEMA
    from alphalab.runtime.run_snapshot import RUN_SCHEMA_HISTORY, RUN_SNAPSHOT_SCHEMA
    from alphalab.runtime.snapshot import PIPELINE_SCHEMA_HISTORY, PIPELINE_SNAPSHOT_SCHEMA

    pairs = [
        (ALLOCATION_SCHEMA_HISTORY, ALLOCATION_SNAPSHOT_SCHEMA),
        (BROKER_SCHEMA_HISTORY, BROKER_SNAPSHOT_SCHEMA),
        (INSTRUMENT_SCHEMA_HISTORY, INSTRUMENT_SNAPSHOT_SCHEMA),
        (LIFECYCLE_SCHEMA_HISTORY, LIFECYCLE_SNAPSHOT_SCHEMA),
        (OMS_SCHEMA_HISTORY, OMS_SNAPSHOT_SCHEMA),
        (FX_FEED_SCHEMA_HISTORY, FX_FEED_SNAPSHOT_SCHEMA),
        (PORTFOLIO_SCHEMA_HISTORY, PORTFOLIO_SNAPSHOT_SCHEMA),
        (LIVE_SCHEMA_HISTORY, LIVE_SNAPSHOT_SCHEMA),
        (RUN_SCHEMA_HISTORY, RUN_SNAPSHOT_SCHEMA),
        (PIPELINE_SCHEMA_HISTORY, PIPELINE_SNAPSHOT_SCHEMA),
    ]
    for history, current in pairs:
        assert history.current == current, history.subsystem
        assert history.readable[-1] == current
        # Every earlier version has exactly one step: upgraded, or refused with a reason.
        assert [step.from_version for step in history.steps] == list(range(1, current))


def _history() -> SchemaHistory:
    return SchemaHistory(
        "demo",
        3,
        (
            SchemaStep(1, "version 2 added b", upgrade=lambda payload: {**payload, "b": 0}),
            SchemaStep(2, "version 3 added c", upgrade=lambda payload: {**payload, "c": 0}),
        ),
    )


def test_steps_compose_in_order_and_stamp_each_version() -> None:
    upgraded = _history().upgrade({"schema_version": 1, "a": 1})

    assert upgraded == {"schema_version": 3, "a": 1, "b": 0, "c": 0}


def test_a_current_payload_is_returned_as_it_is() -> None:
    payload = {"schema_version": 3, "a": 1}

    assert _history().upgrade(payload) is payload


def test_read_reports_the_version_a_payload_started_at() -> None:
    upgraded, origin = _history().read({"schema_version": 2, "a": 1, "b": 5})

    assert origin == 2
    assert upgraded["b"] == 5 and upgraded["c"] == 0


@pytest.mark.parametrize("version", [0, 4, -1])
def test_a_version_outside_the_history_is_refused(version: int) -> None:
    with pytest.raises(StateDecodeError, match=f"declares schema version {version}"):
        _history().upgrade({"schema_version": version})


@pytest.mark.parametrize("version", ["1", 1.0, True, None])
def test_a_malformed_version_is_refused(version: object) -> None:
    with pytest.raises(StateDecodeError, match="not an integer"):
        declared_version({"schema_version": version}, "demo")


def test_a_refused_step_says_why() -> None:
    history = SchemaHistory(
        "demo", 2, (SchemaStep(1, "version 2 added who", refusal="nobody recorded who"),)
    )

    with pytest.raises(SchemaUpgradeRefused, match="nobody recorded who"):
        history.upgrade({"schema_version": 1})


def test_a_history_with_a_gap_is_refused_at_definition() -> None:
    with pytest.raises(ValueError, match="one step for each"):
        SchemaHistory(
            "demo", 3, (SchemaStep(2, "version 3 added c", upgrade=lambda payload: payload),)
        )


def test_a_step_is_an_upgrade_or_a_refusal_never_both() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SchemaStep(1, "both", upgrade=lambda payload: payload, refusal="and a reason")
    with pytest.raises(ValueError, match="exactly one"):
        SchemaStep(1, "neither")
