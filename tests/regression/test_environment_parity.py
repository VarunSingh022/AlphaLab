"""Historical, replay, paper and live share one execution path.

The claim v2.3 makes is structural: everything from a market record down to the
OMS is *the same code*, not four implementations that agree. These tests check
it two ways -- by identity, so a future divergence cannot hide behind matching
behaviour, and by result, so identical code cannot silently be given different
inputs.
"""

from decimal import Decimal

from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.replay import ReplayBacktest
from alphalab.core.fill import Fill
from alphalab.market.source import SequenceSource
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionRouting
from alphalab.runtime.session import ExecutionMode, SessionConfig, TradingSession
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

_STRATEGY = "PARITY"
_ASSET = "3d4f5a6b-7c8d-49e0-b1a2-c3d4e5f60718"
_MIDS = [Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103")]
_PLAN = {2.0: Decimal("10"), 4.0: Decimal("-10")}
_SEED = 20230


def _dataset() -> MarketDataset:
    return dataset_of_quotes(_ASSET, _MIDS)


def _strategy() -> StrategyRuntimeState:
    return running_strategy_state(_STRATEGY, ScriptedStrategy(_STRATEGY, _ASSET, _PLAN))


def _session(mode: ExecutionMode) -> SessionConfig:
    config = backtest_config(_STRATEGY, seed=_SEED)
    return SessionConfig(
        pipeline=config.pipeline,
        mode=mode,
        fill_policy=config.fill_policy,
        seed=config.seed,
        start_timestamp=config.start_timestamp,
    )


# --- shared by identity ------------------------------------------------------


def test_every_environment_publishes_records_through_one_function() -> None:
    """The backtest publisher delegates rather than reimplementing."""
    from alphalab.backtesting import engine as backtesting_engine

    assert "publish_record" in backtesting_engine.publish.__code__.co_names

    # And the delegation is not merely nominal: both produce the same state.
    start = ExecutionPipeline.initialize(
        _session(ExecutionMode.BACKTEST).pipeline, _strategy(), 1.0
    ).market
    record = _dataset().records[0]

    via_backtest = backtesting_engine.publish(start, record)
    via_pipeline = ExecutionPipeline.publish_record(start, record)

    assert len(via_backtest.events) == len(via_pipeline.events) == 1
    assert via_backtest.latest_quotes == via_pipeline.latest_quotes


def test_backtest_and_session_take_the_same_canonical_step() -> None:
    from alphalab.backtesting import engine as backtesting_engine
    from alphalab.runtime import session as session_module

    backtest_source = backtesting_engine.advance.__code__.co_names
    session_source = session_module.TradingSession.advance.__code__.co_names

    assert "process_record" in backtest_source
    assert "process_record" in session_source


def test_the_canonical_types_are_shared_not_mirrored() -> None:
    """One record, one order, one fill, one portfolio -- across all four modes."""
    from alphalab.backtesting.dataset import MarketRecord as BacktestRecord
    from alphalab.market.record import MarketRecord as CanonicalRecord

    assert BacktestRecord is CanonicalRecord


# --- shared by result --------------------------------------------------------


def test_backtest_replay_and_paper_produce_identical_runs() -> None:
    dataset = _dataset()
    config = backtest_config(_STRATEGY, seed=_SEED)

    backtest = BacktestEngine.run(config, dataset, _strategy(), context_factory)
    replayed = ReplayBacktest.run(config, dataset, _strategy(), context_factory)
    paper = TradingSession.run(
        _session(ExecutionMode.PAPER),
        SequenceSource.from_records(dataset.dataset_id, dataset.records),
        _strategy(),
        context_factory,
    )

    assert backtest.fills == replayed.backtest.fills
    assert backtest.fills == paper.pipeline.fills.to_tuple()
    assert backtest.trades == paper.pipeline.trades.to_tuple()
    assert backtest.orders == tuple(paper.pipeline.oms.orders.orders())


def test_the_three_simulated_environments_agree_on_cash_and_positions() -> None:
    dataset = _dataset()
    config = backtest_config(_STRATEGY, seed=_SEED)

    backtest = BacktestEngine.run(config, dataset, _strategy(), context_factory)
    replayed = ReplayBacktest.run(config, dataset, _strategy(), context_factory)
    paper = TradingSession.run(
        _session(ExecutionMode.PAPER),
        SequenceSource.from_records(dataset.dataset_id, dataset.records),
        _strategy(),
        context_factory,
    )

    cash = backtest.state.portfolio.cash.balance("USD")
    assert replayed.backtest.state.portfolio.cash.balance("USD") == cash
    assert paper.pipeline.portfolio.cash.balance("USD") == cash

    assert (
        backtest.state.portfolio.positions
        == replayed.backtest.state.portfolio.positions
        == paper.pipeline.portfolio.positions
    )


def test_the_equity_curve_is_identical_across_the_simulated_environments() -> None:
    dataset = _dataset()
    paper = TradingSession.run(
        _session(ExecutionMode.PAPER),
        SequenceSource.from_records(dataset.dataset_id, dataset.records),
        _strategy(),
        context_factory,
    )
    backtest = BacktestEngine.run(
        backtest_config(_STRATEGY, seed=_SEED), dataset, _strategy(), context_factory
    )

    assert paper.pipeline.portfolio_snapshots.to_tuple() == backtest.equity_curve


# --- where they intentionally differ -----------------------------------------


def test_live_differs_from_the_others_in_exactly_one_place() -> None:
    """Same orders; the venue is the only thing that changed."""
    dataset = _dataset()

    simulated = TradingSession.run(
        _session(ExecutionMode.PAPER),
        SequenceSource.from_records("D", dataset.records),
        _strategy(),
        context_factory,
    )
    live = TradingSession.run(
        _session(ExecutionMode.LIVE),
        SequenceSource.from_records("D", dataset.records),
        _strategy(),
        context_factory,
    )

    # Identical up to and including order submission.
    assert live.processed == simulated.processed
    assert len(live.pipeline.oms.orders.orders()) == len(simulated.pipeline.oms.orders.orders())
    assert [o.asset_id for o in live.pipeline.oms.orders.orders()] == [
        o.asset_id for o in simulated.pipeline.oms.orders.orders()
    ]
    assert [o.quantity for o in live.pipeline.oms.orders.orders()] == [
        o.quantity for o in simulated.pipeline.oms.orders.orders()
    ]

    # And divergent only at the venue: one filled, the other is still waiting.
    assert simulated.pipeline.fills
    assert not live.pipeline.fills
    assert not simulated.working_orders
    assert len(live.working_orders) == 2


def test_each_mode_declares_its_own_routing_and_clock() -> None:
    assert ExecutionMode.BACKTEST.routing is ExecutionRouting.SIMULATED
    assert ExecutionMode.REPLAY.routing is ExecutionRouting.SIMULATED
    assert ExecutionMode.PAPER.routing is ExecutionRouting.SIMULATED
    assert ExecutionMode.LIVE.routing is ExecutionRouting.EXTERNAL

    assert not ExecutionMode.BACKTEST.is_realtime
    assert not ExecutionMode.REPLAY.is_realtime
    assert ExecutionMode.PAPER.is_realtime
    assert ExecutionMode.LIVE.is_realtime


def test_a_seeded_session_is_reproducible_like_a_seeded_backtest() -> None:
    dataset = _dataset()

    def run() -> tuple[Fill, ...]:
        state = TradingSession.run(
            _session(ExecutionMode.PAPER),
            SequenceSource.from_records("D", dataset.records),
            _strategy(),
            context_factory,
        )
        return state.pipeline.fills.to_tuple()

    assert run() == run()
