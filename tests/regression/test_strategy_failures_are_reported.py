"""A run says when a strategy failed, and can stop when one does (ledger EXE-006).

A strategy whose hook raises, or that emits an invalid intent, is moved to
``FAILED`` and dispatched nothing more, and the run goes on with the others --
the isolation the strategy runtime has always promised. Until v3.10 nothing on
the run or its result said so: a backtest whose only strategy crashed on its
first record returned a flat equity curve that read as a strategy that chose not
to trade.
"""

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from alphalab.backtesting import BacktestEngine, MarketDataset
from alphalab.persistence import deserialize, serialize
from alphalab.runtime import StrategyFailedError, StrategyFailure
from alphalab.runtime.run_snapshot import capture, from_primitives
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, LifecycleTransitioned
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = str(UUID(int=0xFA11))
ASSET = str(UUID(int=0xD, version=4))


class _Raises(BaseStrategy):
    """Buys at t=2, raises at t=3, and would buy again at t=4 if it were asked."""

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        timestamp = event.quote.timestamp
        if timestamp == 3.0:
            raise RuntimeError("the model file was missing")
        return (Intent(STRATEGY_ID, ASSET, Decimal("1"), timestamp=timestamp),)


class _Invalid(BaseStrategy):
    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return (Intent(STRATEGY_ID, ASSET, Decimal("1"), strength=Decimal("2")),)


def _dataset() -> MarketDataset:
    return MarketDataset.of(
        "DS-SF",
        [sized_quote(ASSET, 2.0 + step, Decimal("100"), Decimal("10")) for step in range(4)],
    )


def _run(strategy: BaseStrategy, **config: Any) -> Any:
    return BacktestEngine.run(
        replace(backtest_config(STRATEGY_ID, seed=1), **config),
        _dataset(),
        running_strategy_state(STRATEGY_ID, strategy),
        context_factory,
    )


def test_a_clean_run_reports_no_failure() -> None:
    result = _run(ScriptedStrategy(STRATEGY_ID, ASSET, {2.0: Decimal("1")}))

    assert result.strategy_failures == ()


def test_a_raising_hook_is_reported_with_its_instant_and_error() -> None:
    result = _run(_Raises())

    assert result.strategy_failures == (
        StrategyFailure(STRATEGY_ID, 3.0, "HookExecutionError: the model file was missing"),
    )
    # Isolated, as before: the run finished, and the failed strategy traded
    # only before it failed.
    assert result.records_processed == 4
    assert [fill.filled_at for fill in result.fills] == [2.0]


def test_an_invalid_intent_is_reported_as_a_failure() -> None:
    result = _run(_Invalid())

    (failure,) = result.strategy_failures
    assert (failure.strategy_id, failure.timestamp) == (STRATEGY_ID, 2.0)
    assert "strength" in failure.error.lower()


def test_a_run_configured_to_halt_stops_after_the_failing_record() -> None:
    with pytest.raises(StrategyFailedError, match="the model file was missing") as excinfo:
        _run(_Raises(), halt_on_strategy_failure=True)

    error = excinfo.value
    assert error.failures == (
        StrategyFailure(STRATEGY_ID, 3.0, "HookExecutionError: the model file was missing"),
    )
    # The run as it stood after the failing record: two records processed,
    # the fill before the failure kept, the failure on the state.
    assert error.state.processed == 2
    assert len(error.state.pipeline.fills) == 1
    assert error.state.strategy_failures == error.failures


def test_failures_and_the_halt_setting_survive_a_snapshot() -> None:
    result = _run(_Raises(), halt_on_strategy_failure=False)
    payload = deserialize(serialize(capture(result.run)))

    assert payload["halt_on_strategy_failure"] is False
    snapshot = from_primitives(payload)
    assert snapshot.halt_on_strategy_failure is False
    failed = [
        record.event
        for record in snapshot.pipeline.strategy_events
        if isinstance(record.event, LifecycleTransitioned) and record.event.new_state == "FAILED"
    ]
    assert [(event.strategy_id, event.timestamp) for event in failed] == [(STRATEGY_ID, 3.0)]
