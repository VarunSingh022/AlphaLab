"""When a simulated order fills, and that a run says so (ledger EXE-001).

Until v3.10 every simulated order filled at the event that decided it -- at the
bar close or quote midpoint the strategy had just observed. No real order
trades at the price that triggered it, so every simulated result was optimistic
by construction, and nothing in it said so.

:class:`~alphalab.execution.policy.FillTiming` makes the timing a first-class,
recorded choice. ``SAME_EVENT`` is the v3.9 behaviour, and stays the default so
a v3.9 run means what it meant. ``NEXT_EVENT`` leaves the order working until
its asset's next event and fills it there, before the strategy is dispatched on
that event -- so it decides on one observation and trades on the next.
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from alphalab.backtesting import BacktestEngine, MarketDataset, ReplayBacktest
from alphalab.common.ids import id_scope
from alphalab.execution.fill import FillStatus
from alphalab.execution.policy import FillDecision, FillTiming, LiquidityContext
from alphalab.market.record import MarketRecord
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineState
from alphalab.runtime.run import RunConfig
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.run_snapshot import from_primitives as run_from_primitives
from alphalab.runtime.run_snapshot import restore as restore_run
from alphalab.runtime.session import TradingSession
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = str(UUID(int=0xF1D1))
ASSET = str(UUID(int=0xA, version=4))
OTHER = str(UUID(int=0xB, version=4))


def _pipeline(
    plan: Mapping[float, Decimal], timing: FillTiming, asset_for: Mapping[float, str] | None = None
) -> ExecutionPipelineState:
    config = replace(pipeline_config(STRATEGY_ID), fill_timing=timing)
    strategy = ScriptedStrategy(STRATEGY_ID, ASSET, plan, asset_for)
    return ExecutionPipeline.initialize(config, running_strategy_state(STRATEGY_ID, strategy), 1.0)


def _quote(state: ExecutionPipelineState, asset: str, timestamp: float, mid: str, **kwargs: Any):  # type: ignore[no-untyped-def]
    return ExecutionPipeline.process_quote(
        state,
        sized_quote(asset, timestamp, Decimal(mid), Decimal("1000")),
        context_factory,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# The two timings
# --------------------------------------------------------------------------- #


def test_same_event_fills_at_the_price_that_decided_it() -> None:
    result = _quote(_pipeline({2.0: Decimal("10")}, FillTiming.SAME_EVENT), ASSET, 2.0, "100")

    (fill,) = result.fills
    assert (fill.price, fill.filled_at) == (Decimal("100"), 2.0)
    assert not result.state.oms.active_orders


def test_next_event_leaves_the_order_working_and_fills_it_at_the_next_price() -> None:
    state = _pipeline({2.0: Decimal("10")}, FillTiming.NEXT_EVENT)

    decided = _quote(state, ASSET, 2.0, "100")
    assert decided.fills == ()
    assert len(decided.oms_orders) == 1
    assert len(decided.state.oms.active_orders) == 1

    filled = _quote(decided.state, ASSET, 3.0, "105")
    (fill,) = filled.fills
    assert (fill.price, fill.filled_at) == (Decimal("105"), 3.0)
    assert not filled.state.oms.active_orders
    assert filled.state.portfolio.positions[ASSET].quantity == Decimal("10")
    # The reservation made at the decision is consumed by the fill.
    assert dict(filled.state.allocation.reservations) == {}


def test_next_event_waits_for_the_orders_own_asset() -> None:
    state = _pipeline({2.0: Decimal("10")}, FillTiming.NEXT_EVENT)
    state = _quote(state, ASSET, 2.0, "100").state

    other = _quote(state, OTHER, 2.5, "50")
    assert other.fills == ()
    assert len(other.state.oms.active_orders) == 1

    own = _quote(other.state, ASSET, 3.0, "101")
    assert [fill.price for fill in own.fills] == [Decimal("101")]


class _Watcher(BaseStrategy):
    """Buys once at t=2 and records the position it is shown at every quote."""

    def __init__(self) -> None:
        self.seen: list[tuple[float, Decimal]] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.seen.append((event.quote.timestamp, context.portfolio.quantity(ASSET)))
        if event.quote.timestamp == 2.0:
            return (Intent(STRATEGY_ID, ASSET, Decimal("10"), timestamp=2.0),)
        return ()


def test_the_strategy_decides_on_a_book_that_includes_the_fill() -> None:
    watcher = _Watcher()
    config = replace(pipeline_config(STRATEGY_ID), fill_timing=FillTiming.NEXT_EVENT)
    state = ExecutionPipeline.initialize(config, running_strategy_state(STRATEGY_ID, watcher), 1.0)
    for timestamp, mid in ((2.0, "100"), (3.0, "102")):
        state = _quote(state, ASSET, timestamp, mid).state

    # Nothing held when deciding at t=2; the fill at t=3 happened before t=3's
    # dispatch, so the strategy saw it there.
    assert watcher.seen == [(2.0, Decimal("0")), (3.0, Decimal("10"))]


def test_an_order_still_working_at_the_end_is_not_filled() -> None:
    state = _pipeline({2.0: Decimal("10")}, FillTiming.NEXT_EVENT)
    state = _quote(state, ASSET, 2.0, "100").state

    assert state.fills == ()
    (order_id,) = tuple(state.oms.active_orders)
    assert state.oms.orders.find(order_id).status.name == "ACCEPTED"


class _NoFill:
    def decide(self, context: LiquidityContext) -> FillDecision:
        return FillDecision(FillStatus.NO_FILL)


class _Half:
    def decide(self, context: LiquidityContext) -> FillDecision:
        return FillDecision(FillStatus.PARTIAL_FILL, context.requested_quantity / 2)


def test_a_next_event_order_the_venue_does_not_fill_is_closed_and_released() -> None:
    state = _pipeline({2.0: Decimal("10")}, FillTiming.NEXT_EVENT)
    state = _quote(state, ASSET, 2.0, "100").state

    result = _quote(state, ASSET, 3.0, "100", fill_policy=_NoFill())

    assert result.fills == ()
    assert not result.state.oms.active_orders
    assert dict(result.state.allocation.reservations) == {}


def test_a_partial_next_event_fill_withdraws_the_remainder() -> None:
    state = _pipeline({2.0: Decimal("10")}, FillTiming.NEXT_EVENT)
    state = _quote(state, ASSET, 2.0, "100").state

    result = _quote(state, ASSET, 3.0, "100", fill_policy=_Half())

    assert [fill.quantity for fill in result.fills] == [Decimal("5")]
    assert not result.state.oms.active_orders
    assert dict(result.state.allocation.reservations) == {}


# --------------------------------------------------------------------------- #
# Recorded, durable, and the same in every driver
# --------------------------------------------------------------------------- #


def _records() -> list[MarketRecord]:
    return [
        MarketRecord(
            "DS-FT",
            2.0 + step,
            sized_quote(ASSET, 2.0 + step, Decimal(100 + step % 3), Decimal("1000")),
        )
        for step in range(8)
    ]


PLAN = {2.0: Decimal("10"), 4.0: Decimal("-4"), 6.0: Decimal("3")}


def _config() -> RunConfig:
    base = backtest_config(STRATEGY_ID, seed=909)
    return replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))


def _drive(state: Any, records: list[MarketRecord]) -> Any:
    for record in records:
        state, _ = TradingSession.advance(state, record, context_factory)
    return state


def test_a_run_resumed_between_decision_and_fill_equals_an_uninterrupted_one() -> None:
    records = _records()
    with id_scope(909):
        control = _drive(
            TradingSession.initialize(
                _config(),
                running_strategy_state(STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET, PLAN)),
            ),
            records,
        )
    with id_scope(909):
        partial = _drive(
            TradingSession.initialize(
                _config(),
                running_strategy_state(STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET, PLAN)),
            ),
            records[:1],
        )
    assert len(partial.pipeline.oms.active_orders) == 1, "captured with the order working"

    payload = deserialize(serialize(capture_run(partial)))
    assert payload["pipeline"]["config"]["fill_timing"] == "next_event"
    strategy = ScriptedStrategy(STRATEGY_ID, ASSET, PLAN)
    restored = restore_run(
        run_from_primitives(payload),
        RunObjects(
            pipeline=RuntimeObjects(
                sizing_model=_config().pipeline.sizing_model,
                simulator=_config().pipeline.simulator,
                strategies={STRATEGY_ID: strategy},
                instruments=None,
            ),
            fill_policy=_config().fill_policy,
        ),
    )
    assert restored.pipeline.oms.working_orders_for(
        ASSET
    ) == partial.pipeline.oms.working_orders_for(ASSET)
    with TradingSession.resume(restored):
        resumed = _drive(restored, records[1:])

    assert serialize(capture_run(resumed)) == serialize(capture_run(control))
    assert [fill.filled_at for fill in control.pipeline.fills] == [3.0, 5.0, 7.0]


def test_backtest_and_replay_agree_under_next_event() -> None:
    dataset = MarketDataset.of("DS-FT", [record.payload for record in _records()])

    def strategy() -> Any:
        return running_strategy_state(STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET, PLAN))

    backtest = BacktestEngine.run(_config(), dataset, strategy(), context_factory)
    replay = ReplayBacktest.run(_config(), dataset, strategy(), context_factory)

    assert [(f.price, f.filled_at) for f in backtest.fills] == [
        (f.price, f.filled_at) for f in replay.backtest.fills
    ]
    assert backtest.equity_curve == replay.backtest.equity_curve


@pytest.mark.parametrize("timing", list(FillTiming))
def test_the_timing_is_part_of_the_configuration_a_snapshot_carries(timing: FillTiming) -> None:
    from alphalab.runtime.snapshot import capture, from_primitives

    state = _pipeline({2.0: Decimal("1")}, timing)
    payload = deserialize(serialize(capture(state)))

    assert payload["config"]["fill_timing"] == timing.value
    assert from_primitives(payload).config.fill_timing is timing
