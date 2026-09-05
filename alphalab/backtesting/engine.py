"""The unified backtest loop.

This is the one place a dataset is turned into a run. It composes the existing
engines rather than replacing any of them: every record is published to the
market engine and then handed to
:meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.process_market_event`,
which is the same call the rest of AlphaLab's execution path makes. There is no
backtest-only order model, no backtest-only fill model, and -- most importantly
-- no backtest-only portfolio accounting: cash, positions, realized and
unrealized P&L come from :class:`~alphalab.portfolio.engine.PortfolioEngine`,
exactly once per fill.

::

    MarketDataset
      -> MarketEngine.publish_*        -> MarketEvent
      -> ExecutionPipeline.process_market_event
           -> mark to market -> risk resync
           -> StrategyEngine -> AllocationEngine -> RiskEngine
           -> OMSEngine -> ExecutionEngine (FillPolicy) -> PortfolioEngine
      -> AnalyticsEngine.compile_report

:func:`advance` is the canonical step. :class:`BacktestEngine` drives it from a
dataset; :mod:`alphalab.backtesting.replay` drives the very same function from
the replay cursor, which is why the two paths cannot diverge.
"""

from __future__ import annotations

from dataclasses import replace

from alphalab.backtesting.config import BacktestConfig
from alphalab.backtesting.dataset import MarketDataset, MarketRecord
from alphalab.backtesting.state import BacktestResult, BacktestState, BacktestStep
from alphalab.common.ids import id_scope, id_source
from alphalab.market.state import MarketState
from alphalab.runtime.execution_pipeline import (
    ContextFactory,
    ExecutionPipeline,
    ExecutionPipelineResult,
)
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "BacktestEngine",
    "advance",
    "finalize",
    "id_scope",
    "id_source",
    "initialize",
    "publish",
]

# ``id_scope`` and ``id_source`` are defined in :mod:`alphalab.common.ids` as of
# v2.3 and re-exported here unchanged. They moved so that a session outside this
# package -- :mod:`alphalab.runtime.session` -- can mint reproducible
# identifiers without importing the backtesting engine, which imports it.


def publish(market: MarketState, record: MarketRecord) -> MarketState:
    """Publish one dataset record to the market engine.

    Delegates to :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.publish_record`,
    which every environment publishes through.
    """

    return ExecutionPipeline.publish_record(market, record)


def initialize(config: BacktestConfig, strategy_state: StrategyRuntimeState) -> BacktestState:
    """Fund the portfolio and build the state a run starts from."""

    return BacktestState(
        config=config,
        pipeline=ExecutionPipeline.initialize(
            config.pipeline, strategy_state, config.start_timestamp
        ),
        current_timestamp=config.start_timestamp,
    )


def advance(
    state: BacktestState,
    record: MarketRecord,
    context_factory: ContextFactory,
) -> tuple[BacktestState, ExecutionPipelineResult]:
    """Move one dataset record through the whole execution path, and record it.

    The run-level wrapper around
    :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.process_record`,
    which is the canonical step every environment takes. What this adds is the
    run's own bookkeeping -- which record it was, and what it produced.
    """

    result = ExecutionPipeline.process_record(
        state.pipeline, record, context_factory, state.config.fill_policy
    )

    step = BacktestStep(
        index=state.processed,
        event_id=record.event_id,
        timestamp=record.timestamp,
        orders=result.oms_orders,
        reports=result.execution_reports,
        fills=result.fills,
        equity=result.state.portfolio_snapshots[-1].total_equity,
    )
    return (
        replace(
            state,
            pipeline=result.state,
            processed=state.processed + 1,
            current_timestamp=record.timestamp,
            steps=state.steps.append(step),
        ),
        result,
    )


def finalize(state: BacktestState) -> BacktestResult:
    """Compile analytics (if configured) and freeze the run into a result."""

    pipeline = state.pipeline
    if state.config.compile_analytics:
        pipeline = ExecutionPipeline.compile_analytics(
            pipeline,
            state.current_timestamp,
            state.config.years_elapsed,
            state.config.risk_free_rate,
        )

    return BacktestResult(
        config=state.config,
        state=pipeline,
        steps=state.steps.to_tuple(),
        records_processed=state.processed,
        seed=state.config.seed,
    )


class BacktestEngine:
    """Runs a dataset through the execution path, deterministically."""

    @staticmethod
    def initialize(config: BacktestConfig, strategy_state: StrategyRuntimeState) -> BacktestState:
        """Fund the portfolio and build the state a run starts from."""

        return initialize(config, strategy_state)

    @staticmethod
    def advance(
        state: BacktestState,
        record: MarketRecord,
        context_factory: ContextFactory,
    ) -> tuple[BacktestState, ExecutionPipelineResult]:
        """Move one record through the path. See :func:`advance`."""

        return advance(state, record, context_factory)

    @staticmethod
    def finalize(state: BacktestState) -> BacktestResult:
        """Compile analytics and freeze the run. See :func:`finalize`."""

        return finalize(state)

    @staticmethod
    def run(
        config: BacktestConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
    ) -> BacktestResult:
        """Run ``dataset`` end to end and return the finished result."""

        with id_scope(config.seed):
            state = initialize(config, strategy_state)
            for record in dataset.records:
                state, _ = advance(state, record, context_factory)
            return finalize(state)
