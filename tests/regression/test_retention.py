"""A run that keeps less history holds exactly the same book (ledger PRF-004).

A :class:`~alphalab.runtime.retention.RetentionPolicy` bounds the derived
histories a run keeps -- market events, steps, audit trails, results -- so that
a months-long session does not grow without end. What it may change is stated
precisely, and these tests hold it to that statement:

* every piece of state a step computes from is the same as the same run's
  without a policy, record for record;
* each bounded log holds exactly the newest entries of the unbounded run's log,
  no more than its bound plus the slack, and counts the rest as dropped;
* a strategy's history window is exact, and an answer it cannot give is refused
  rather than shortened;
* the policy and every dropped count travel in the snapshot, so a stopped run
  continues into the same state as one that never stopped.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.common.ids import id_scope
from alphalab.market.record import MarketRecord
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.exceptions import HistoryNotRetainedError, RuntimeValidationError
from alphalab.runtime.retention import RetentionPolicy, slack
from alphalab.runtime.run import ExecutionMode, RunConfig, RunState
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.run_snapshot import from_primitives as run_from_primitives
from alphalab.runtime.run_snapshot import restore as restore_run
from alphalab.runtime.session import TradingSession
from alphalab.runtime.snapshot import RETAINED_LOGS, RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

SEED = 20261003
STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())
RECORDS = 160
WINDOW = 24
POLICY = RetentionPolicy(market_history=WINDOW, steps=10, audit_events=16, results=12)


def _records(count: int = RECORDS) -> list[MarketRecord]:
    return [
        MarketRecord(
            event_id=f"REC-{index}",
            timestamp=2.0 + index,
            payload=sized_quote(ASSET_ID, 2.0 + index, Decimal(100 + index % 7), Decimal("100")),
        )
        for index in range(count)
    ]


def _plan(count: int = RECORDS) -> dict[float, Decimal]:
    return {
        2.0 + index: (Decimal("5") if index % 3 == 0 else Decimal("-2"))
        for index in range(count)
        if index % 2 == 0
    }


class _Reader(ScriptedStrategy):
    """Trades its plan, and reads the last ``depth`` quotes at every quote."""

    def __init__(self, depth: int) -> None:
        super().__init__(STRATEGY_ID, ASSET_ID, _plan())
        self.depth = depth
        self.seen: list[tuple[Decimal, ...]] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quotes = context.history.quotes(ASSET_ID, limit=self.depth)
        self.seen.append(tuple(quote.bid for quote in quotes))
        return super().on_quote(context, event)


def _config(policy: RetentionPolicy | None) -> RunConfig:
    pipeline = pipeline_config(STRATEGY_ID)
    if policy is not None:
        pipeline = replace(pipeline, retention=policy)
    return RunConfig(pipeline=pipeline, mode=ExecutionMode.BACKTEST, seed=SEED, start_timestamp=1.0)


def _run(
    policy: RetentionPolicy | None, strategy: ScriptedStrategy | None = None
) -> list[RunState]:
    """Every state the run passes through, one per record."""

    strategy = strategy or ScriptedStrategy(STRATEGY_ID, ASSET_ID, _plan())
    states: list[RunState] = []
    with id_scope(SEED):
        state = TradingSession.initialize(
            _config(policy), running_strategy_state(STRATEGY_ID, strategy)
        )
        for record in _records():
            state, _ = TradingSession.advance(state, record, context_factory)
            states.append(state)
    return states


def _book(state: RunState) -> dict[str, Any]:
    """What a step computes from -- everything a policy must leave alone."""

    p = state.pipeline
    return {
        "positions": {k: v.quantity for k, v in p.portfolio.positions.items()},
        "cash": p.portfolio.cash.balance("USD"),
        "reserved": dict(p.portfolio.cash.reserved),
        "realized_pnl": p.portfolio.realized_pnl,
        "commission": p.portfolio.commission_paid,
        "reservations": dict(p.allocation.reservations),
        "notional_allocated": p.allocation.notional_allocated,
        "orders": list(p.oms.orders.orders()),
        "active_orders": set(p.oms.active_orders),
        "execution_reports": dict(p.execution.reports),
        "risk": (p.risk.cash, p.risk.buying_power, p.risk.peak_nav, p.risk.current_nav),
        "latest_quotes": dict(p.market.latest_quotes),
        "market_prices": dict(p.market_prices),
        "id_position": p.id_position,
        "processed": state.processed,
        "equity": p.portfolio_snapshots[-1].total_equity,
    }


def _logs(state: RunState) -> dict[str, Any]:
    from alphalab.runtime.snapshot import _RETAINED

    logs = {path: read(state.pipeline) for path, read, _ in _RETAINED}
    logs["steps"] = state.steps
    logs["skipped"] = state.skipped
    return logs


def _bound(path: str, policy: RetentionPolicy) -> int | None:
    if path.startswith("market."):
        return policy.market_history
    if path in ("steps", "skipped"):
        return policy.steps
    if path in ("fills", "trades", "trade_records", "portfolio_snapshots") or path.endswith(
        "transactions"
    ):
        return policy.results
    return policy.audit_events


def test_a_policy_changes_no_state_a_step_computes_from() -> None:
    unbounded = _run(None)
    bounded = _run(POLICY)

    for index, (full, kept) in enumerate(zip(unbounded, bounded, strict=True)):
        assert _book(kept) == _book(full), f"record {index}"


def test_each_bounded_log_holds_exactly_the_newest_entries() -> None:
    full = _run(None)[-1]
    kept = _run(POLICY)[-1]
    trimmed_any = False

    for path, log in _logs(kept).items():
        whole = _logs(full)[path]
        bound = _bound(path, POLICY)
        assert bound is not None
        assert len(log) <= bound + slack(bound), path
        assert log.dropped + len(log) == len(whole), path
        assert log.to_tuple() == whole.to_tuple()[log.dropped :], path
        trimmed_any = trimmed_any or log.dropped > 0
    assert trimmed_any, "the workload should outgrow the bounds"
    assert set(RETAINED_LOGS) <= set(_logs(kept))


def test_a_log_is_trimmed_back_to_its_bound_only_past_the_slack() -> None:
    policy = RetentionPolicy(steps=8)  # slack 1: trimmed at 10, back to 8
    lengths = [len(state.steps) for state in _run(policy)[:12]]

    assert lengths == [1, 2, 3, 4, 5, 6, 7, 8, 9, 8, 9, 8]


def test_the_default_policy_keeps_everything_as_every_earlier_run_did() -> None:
    state = _run(RetentionPolicy())[-1]

    assert RetentionPolicy().keeps_everything
    assert all(log.dropped == 0 for log in _logs(state).values())
    assert len(state.steps) == RECORDS


def test_a_retained_run_round_trips_and_continues_where_it_stopped() -> None:
    boundary = 97
    reference = _run(POLICY)[-1]

    strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, _plan())
    config = _config(POLICY)
    records = _records()
    with id_scope(SEED):
        state = TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
        for record in records[:boundary]:
            state, _ = TradingSession.advance(state, record, context_factory)

    payload = serialize(capture_run(state))
    objects = RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY_ID: strategy},
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )
    restored = restore_run(run_from_primitives(deserialize(payload)), objects)
    assert restored == state
    assert restored.config.pipeline.retention == POLICY
    assert {p: log.dropped for p, log in _logs(restored).items()} == {
        p: log.dropped for p, log in _logs(state).items()
    }

    with TradingSession.resume(restored):
        for record in records[boundary:]:
            restored, _ = TradingSession.advance(restored, record, context_factory)

    assert _book(restored) == _book(reference)
    assert {p: (log.dropped, log.to_tuple()) for p, log in _logs(restored).items()} == {
        p: (log.dropped, log.to_tuple()) for p, log in _logs(reference).items()
    }


def test_the_history_window_answers_exactly_what_an_unbounded_run_would() -> None:
    within = WINDOW // 2
    unbounded = _Reader(within)
    bounded = _Reader(within)
    _run(None, unbounded)
    _run(POLICY, bounded)

    assert bounded.seen == unbounded.seen
    assert len(bounded.seen[-1]) == within


def test_a_question_older_than_the_window_is_refused_not_answered_short() -> None:
    state = _run(POLICY)[-1]
    history = state.pipeline.market.history
    from alphalab.runtime.context_views import HistoryView

    view = HistoryView(state.pipeline.market, state.current_timestamp, WINDOW)
    assert view.visible == WINDOW
    assert not view.complete
    assert len(view.quotes(ASSET_ID, limit=WINDOW)) == WINDOW
    with pytest.raises(HistoryNotRetainedError, match="outside the retention window"):
        view.quotes(ASSET_ID, limit=WINDOW + 1)
    with pytest.raises(HistoryNotRetainedError, match="every one"):
        view.quotes(ASSET_ID)
    # The window is the policy's, not however long the log happens to be.
    assert len(history) >= WINDOW


def test_a_strategy_asking_past_the_window_fails_and_is_recorded() -> None:
    reader = _Reader(WINDOW + 5)
    states = _run(POLICY, reader)

    failures = states[-1].strategy_failures
    assert failures
    assert "retention window" in failures[0].error


@pytest.mark.parametrize("bound", [0, -1, True, 1.5])
def test_a_bound_is_a_positive_whole_number(bound: object) -> None:
    with pytest.raises(RuntimeValidationError, match="positive whole number"):
        RetentionPolicy(steps=bound)  # type: ignore[arg-type]
