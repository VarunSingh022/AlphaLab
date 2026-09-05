"""Unit tests for the backtest engine's own mechanics.

The execution semantics themselves are covered by the integration tests; what
is checked here is the loop around them: the step it records, the state it
threads, and the seam where a record becomes a market event.
"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    MarketDataset,
    MarketRecord,
    UnsupportedRecordError,
    advance,
    finalize,
    id_source,
    initialize,
    publish,
)
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.engine import MarketEngine
from alphalab.market.tick import Tick
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

MIDS = [Decimal("100.005"), Decimal("101.005"), Decimal("102.005")]


def _run_parts() -> tuple[str, str]:
    return str(uuid4()), str(uuid4())


def test_publish_routes_each_market_input_to_its_engine_method() -> None:
    market = MarketEngine.reset()
    bar = Bar(
        "AAPL",
        1.0,
        Decimal("100"),
        Decimal("101"),
        Decimal("99"),
        Decimal("100.5"),
        Decimal("1000"),
        Decimal("100.2"),
        10,
        TimeFrame.M1,
    )
    tick = Tick("AAPL", 2.0, Decimal("100.5"), Decimal("5"), "T1", "SIM", "USD")

    market = publish(market, MarketRecord("r0", 1.0, bar))
    market = publish(market, MarketRecord("r1", 2.0, tick))

    assert [type(e).__name__ for e in market.events] == ["BarClosed", "TickReceived"]


def test_publish_rejects_a_record_it_cannot_turn_into_a_market_event() -> None:
    record = MarketRecord("r0", 1.0, object())  # type: ignore[arg-type]

    with pytest.raises(UnsupportedRecordError, match="unsupported market input"):
        publish(MarketEngine.reset(), record)


def test_initialize_funds_the_portfolio_before_any_event() -> None:
    strategy_id, asset_id = _run_parts()
    config = backtest_config(strategy_id)
    state = initialize(
        config, running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, {}))
    )

    assert state.processed == 0
    assert state.current_timestamp == config.start_timestamp
    assert state.pipeline.portfolio.cash.balance("USD") == config.pipeline.starting_cash
    assert len(state.pipeline.portfolio_snapshots) == 1


def test_advance_records_one_step_per_record() -> None:
    strategy_id, asset_id = _run_parts()
    config = backtest_config(strategy_id)
    dataset = dataset_of_quotes(asset_id, MIDS)
    state = initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("10")})
        ),
    )

    for record in dataset.records:
        state, _ = advance(state, record, context_factory)

    assert state.processed == 3
    assert [step.event_id for step in state.steps] == ["DS-0", "DS-1", "DS-2"]
    assert [step.timestamp for step in state.steps] == [2.0, 3.0, 4.0]
    assert state.current_timestamp == 4.0


def test_a_step_carries_what_its_record_produced() -> None:
    strategy_id, asset_id = _run_parts()
    config = backtest_config(strategy_id)
    dataset = dataset_of_quotes(asset_id, MIDS)
    state = initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {3.0: Decimal("10")})
        ),
    )
    for record in dataset.records:
        state, _ = advance(state, record, context_factory)

    trading, quiet = state.steps[1], state.steps[0]

    assert len(trading.orders) == 1
    assert len(trading.fills) == 1
    assert trading.reports[0].fill_quantity == Decimal("10")
    assert quiet.orders == () and quiet.fills == ()
    assert quiet.equity > Decimal("0")


def test_advance_leaves_the_state_it_was_given_untouched() -> None:
    strategy_id, asset_id = _run_parts()
    config = backtest_config(strategy_id)
    dataset = dataset_of_quotes(asset_id, MIDS)
    before = initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("10")})
        ),
    )

    after, _ = advance(before, dataset.records[0], context_factory)

    assert before.processed == 0
    assert len(before.steps) == 0
    assert after.processed == 1
    assert before.pipeline.portfolio.positions == {}


def test_finalize_compiles_analytics_when_configured() -> None:
    strategy_id, asset_id = _run_parts()
    dataset = dataset_of_quotes(asset_id, MIDS)
    strategy = ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("10")})

    result = BacktestEngine.run(
        backtest_config(strategy_id),
        dataset,
        running_strategy_state(strategy_id, strategy),
        context_factory,
    )

    assert result.report is not None
    assert result.records_processed == 3
    assert result.seed == 20220


def test_finalize_can_skip_analytics() -> None:
    strategy_id, asset_id = _run_parts()
    dataset = dataset_of_quotes(asset_id, MIDS)
    strategy = ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("10")})

    result = BacktestEngine.run(
        backtest_config(strategy_id, compile_analytics=False),
        dataset,
        running_strategy_state(strategy_id, strategy),
        context_factory,
    )

    assert result.report is None
    assert result.state.analytics.reports == ()


def test_the_engine_class_and_the_module_functions_are_the_same_loop() -> None:
    strategy_id, asset_id = _run_parts()
    config = backtest_config(strategy_id)
    dataset = dataset_of_quotes(asset_id, MIDS)
    plan = {2.0: Decimal("10"), 4.0: Decimal("-3")}

    manual = initialize(
        config, running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan))
    )
    for record in dataset.records:
        manual, _ = advance(manual, record, context_factory)

    from alphalab.backtesting.engine import id_scope

    with id_scope(config.seed):
        driven = initialize(
            config,
            running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)),
        )
        for record in dataset.records:
            driven, _ = advance(driven, record, context_factory)

    assert finalize(manual).valuation.equity == finalize(driven).valuation.equity


def test_an_unseeded_run_keeps_uuid4_identifiers() -> None:
    assert id_source(None) is None
    assert id_source(7) is not None


class _BuyOnFirstBar(BaseStrategy):
    """Buys a fixed quantity on the first bar it sees, then holds."""

    def __init__(self, strategy_id: str, asset_id: str) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._done = False

    def on_bar(self, context: object, event: Any) -> Iterable[Intent]:
        if self._done:
            return ()
        self._done = True
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=Decimal("10"),
                timestamp=event.bar.timestamp,
            ),
        )


def test_a_dataset_of_bars_drives_the_path() -> None:
    strategy_id, asset_id = _run_parts()
    bars = [
        Bar(
            asset_id,
            2.0 + index,
            Decimal("100"),
            Decimal("103"),
            Decimal("99"),
            mid,
            Decimal("1000"),
            Decimal("100.2"),
            10,
            TimeFrame.M1,
        )
        for index, mid in enumerate(MIDS)
    ]
    result = BacktestEngine.run(
        backtest_config(strategy_id),
        MarketDataset.of("BARS", bars),
        running_strategy_state(strategy_id, _BuyOnFirstBar(strategy_id, asset_id)),
        context_factory,
    )

    assert len(result.fills) == 1
    # The bar's close is the price the path trades and marks at.
    assert result.fills[0].price == MIDS[0]
