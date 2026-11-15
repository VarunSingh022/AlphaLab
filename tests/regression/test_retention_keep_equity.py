"""A bounded run that keeps its equity curve whole (v4.3, ADR-0055 section 5).

``RetentionPolicy.keep_equity`` exempts the equity points from the ``results``
bound, so a run that bounds its fills, trades and ledger can still compile
analytics over its whole history. These tests hold it to ADR-0055's statement:

* a policy without it is stored, and so digested, exactly as v4.2.1 stored it;
* with it, every equity point is kept, and everything else the policy bounds is
  bounded exactly as before;
* the analytics compiled from the kept curve equal the same run's with full
  history, value for value, where a policy without it describes only the points
  it kept;
* it travels in the snapshot, written only when true, and a stopped run continues
  into the same state as one that never stopped;
* it is a boolean, refused otherwise, whether constructed or decoded.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alphalab.analytics.engine import AnalyticsEngine
from alphalab.analytics.report import PerformanceReport
from alphalab.analytics.state import AnalyticsState
from alphalab.common.ids import id_scope
from alphalab.common.serialization import to_serializable
from alphalab.market.record import MarketRecord
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.retention import RetentionPolicy
from alphalab.runtime.run import ExecutionMode, RunConfig, RunState
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.run_snapshot import from_primitives as run_from_primitives
from alphalab.runtime.run_snapshot import restore as restore_run
from alphalab.runtime.session import TradingSession
from alphalab.runtime.snapshot import _RETAINED, RuntimeObjects, _retention
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

SEED = 20261114
STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())
RECORDS = 160
BOUNDED = RetentionPolicy(market_history=24, steps=10, audit_events=16, results=12)
KEPT = replace(BOUNDED, keep_equity=True)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots"

#: The texts frozen snapshots hold for a policy, as v3.12 to v4.2.1 wrote them.
BOUNDED_TEXT = '{"audit_events":16,"market_history":24,"results":12,"steps":10}'
DEFAULT_TEXT = '{"audit_events":null,"market_history":null,"results":null,"steps":null}'


def _records() -> list[MarketRecord]:
    # Prices that move every record, so every equity point is a return of its own.
    return [
        MarketRecord(
            event_id=f"REC-{index}",
            timestamp=2.0 + index,
            payload=sized_quote(
                ASSET_ID, 2.0 + index, Decimal(100 + index % 7) + Decimal(index) / 64, Decimal(100)
            ),
        )
        for index in range(RECORDS)
    ]


def _plan() -> dict[float, Decimal]:
    return {
        2.0 + index: (Decimal("5") if index % 3 == 0 else Decimal("-2"))
        for index in range(RECORDS)
        if index % 2 == 0
    }


def _config(policy: RetentionPolicy | None) -> RunConfig:
    pipeline = pipeline_config(STRATEGY_ID)
    if policy is not None:
        pipeline = replace(pipeline, retention=policy)
    return RunConfig(pipeline=pipeline, mode=ExecutionMode.BACKTEST, seed=SEED, start_timestamp=1.0)


def _objects(config: RunConfig, strategy: ScriptedStrategy) -> RunObjects:
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY_ID: strategy},
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )


def _run(policy: RetentionPolicy | None, records: int = RECORDS) -> RunState:
    strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, _plan())
    with id_scope(SEED):
        state = TradingSession.initialize(
            _config(policy), running_strategy_state(STRATEGY_ID, strategy)
        )
        for record in _records()[:records]:
            state, _ = TradingSession.advance(state, record, context_factory)
    return state


def _logs(state: RunState) -> dict[str, Any]:
    logs = {path: read(state.pipeline) for path, read, _ in _RETAINED}
    logs["steps"] = state.steps
    logs["skipped"] = state.skipped
    return logs


def _report(state: RunState) -> PerformanceReport:
    pipeline = state.pipeline
    with id_scope(SEED):
        analytics = AnalyticsEngine.compile_report(
            AnalyticsState(),
            pipeline.portfolio_snapshots,
            pipeline.trade_records,
            state.current_timestamp,
        )
    return analytics.reports[-1]


# --- a policy without it, stored as before ----------------------------------------


def test_a_policy_without_keep_equity_is_stored_exactly_as_v4_2_1_stored_it() -> None:
    frozen_bounded = (FIXTURES / "v3.13.0" / "checkpoints" / "full_47.json").read_text()
    frozen_default = (FIXTURES / "v4.2.1" / "live.json").read_text()
    assert f'"retention":{BOUNDED_TEXT}' in frozen_bounded
    assert f'"retention":{DEFAULT_TEXT}' in frozen_default

    assert serialize(BOUNDED) == BOUNDED_TEXT
    assert serialize(RetentionPolicy()) == DEFAULT_TEXT
    nested = to_serializable(replace(pipeline_config(STRATEGY_ID), retention=BOUNDED))
    assert nested["retention"] == deserialize(BOUNDED_TEXT)


def test_keep_equity_is_written_only_when_it_is_true() -> None:
    assert serialize(KEPT) == (
        '{"audit_events":16,"keep_equity":true,"market_history":24,"results":12,"steps":10}'
    )
    assert serialize(replace(KEPT, keep_equity=False)) == BOUNDED_TEXT


def test_a_run_without_keep_equity_keeps_its_configuration_text() -> None:
    """The configuration a run's identity hashes holds the policy's stored text."""

    payload = serialize(capture_run(_run(BOUNDED, records=8)))

    assert f'"retention":{BOUNDED_TEXT}' in payload
    assert "keep_equity" not in payload


# --- what it keeps, and what it still bounds --------------------------------------


def test_keep_equity_keeps_every_equity_point_and_bounds_everything_else() -> None:
    full = _logs(_run(None))
    bounded = _logs(_run(BOUNDED))
    kept = _logs(_run(KEPT))

    curve = kept["portfolio_snapshots"]
    assert curve.dropped == 0
    assert curve.to_tuple() == full["portfolio_snapshots"].to_tuple()
    assert bounded["portfolio_snapshots"].dropped > 0, "the workload should outgrow the bound"

    for path in kept:
        if path == "portfolio_snapshots":
            continue
        assert (kept[path].dropped, kept[path].to_tuple()) == (
            bounded[path].dropped,
            bounded[path].to_tuple(),
        ), path
    assert kept["fills"].dropped > 0, "the fills are still bounded"


def test_its_analytics_equal_the_full_history_runs_value_for_value() -> None:
    full = _report(_run(None))
    kept = _report(_run(KEPT))
    bounded = _report(_run(BOUNDED))

    # The opening point at the start instant, then one per record.
    assert len(full.returns.period_returns) == RECORDS
    assert kept.returns == full.returns
    assert kept.risk == full.risk
    assert kept.drawdowns == full.drawdowns
    # Without it, analytics describe only the points kept -- what retention said.
    assert len(bounded.returns.period_returns) < len(full.returns.period_returns)
    assert bounded.returns != full.returns


def test_the_equity_bound_names_what_trims_the_curve() -> None:
    assert RetentionPolicy(results=5).equity_bound == 5
    assert RetentionPolicy(results=5, keep_equity=True).equity_bound is None
    assert RetentionPolicy().equity_bound is None
    assert RetentionPolicy(keep_equity=True).keeps_everything
    assert not KEPT.keeps_everything


# --- it travels in the snapshot ---------------------------------------------------


def test_a_kept_curve_round_trips_and_continues_where_it_stopped() -> None:
    boundary = 97
    reference = _run(KEPT)

    strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, _plan())
    config = _config(KEPT)
    records = _records()
    with id_scope(SEED):
        state = TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
        for record in records[:boundary]:
            state, _ = TradingSession.advance(state, record, context_factory)

    payload = serialize(capture_run(state))
    assert '"keep_equity":true' in payload
    restored = restore_run(run_from_primitives(deserialize(payload)), _objects(config, strategy))
    assert restored == state
    assert restored.config.pipeline.retention == KEPT

    with TradingSession.resume(restored):
        for record in records[boundary:]:
            restored, _ = TradingSession.advance(restored, record, context_factory)

    assert {p: (log.dropped, log.to_tuple()) for p, log in _logs(restored).items()} == {
        p: (log.dropped, log.to_tuple()) for p, log in _logs(reference).items()
    }
    assert restored.pipeline.portfolio_snapshots.dropped == 0


def test_a_stored_policy_reads_back_absent_as_false() -> None:
    bounds = deserialize(BOUNDED_TEXT)

    assert _retention(bounds, "config.retention") == BOUNDED
    assert _retention({**bounds, "keep_equity": True}, "config.retention") == KEPT
    assert _retention({**bounds, "keep_equity": False}, "config.retention") == BOUNDED


# --- it is a boolean ----------------------------------------------------------------


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_keep_equity_is_a_boolean(value: object) -> None:
    with pytest.raises(RuntimeValidationError, match="keep_equity"):
        RetentionPolicy(keep_equity=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [1, "true", None])
def test_a_stored_keep_equity_that_is_not_a_boolean_is_refused(value: object) -> None:
    payload = {**deserialize(BOUNDED_TEXT), "keep_equity": value}

    with pytest.raises(StateDecodeError, match=r"config\.retention\.keep_equity"):
        _retention(payload, "config.retention")
