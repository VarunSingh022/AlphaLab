"""The backtest driver: a dataset, and the result it reports.

This is the one place a dataset is turned into a run. It composes no engines of
its own and holds no state: every record goes to
:meth:`~alphalab.runtime.run.RunEngine.advance`, which is the canonical run step
every environment takes, and the state it threads is
:class:`~alphalab.runtime.run.RunState`. There is no backtest-only order model,
no backtest-only fill model, and -- most importantly -- no backtest-only
portfolio accounting: cash, positions, realized and unrealized P&L come from
:class:`~alphalab.portfolio.engine.PortfolioEngine`, exactly once per fill.

::

    MarketDataset
      -> RunEngine.advance                     (the canonical step)
           -> ExecutionPipeline.process_record
                -> mark to market -> risk resync
                -> StrategyEngine -> AllocationEngine -> RiskEngine
                -> OMSEngine -> ExecutionEngine (FillPolicy) -> PortfolioEngine
      -> RunEngine.finalize                    (AnalyticsEngine.compile_report)
      -> BacktestResult                        (a projection, not a copy)

:mod:`alphalab.backtesting.replay` drives the very same function from the replay
cursor, and :class:`~alphalab.runtime.session.TradingSession` drives it from a
market-data source, which is why the three paths cannot diverge.

Until v2.14 this module owned ``BacktestState`` and ``BacktestConfig``, and
:mod:`alphalab.runtime.session` owned a near-identical pair. ADR-0030 gives the
run one owner; what is left here is a driver.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import replace

from alphalab.backtesting.dataset import MarketDataset, MarketRecord
from alphalab.backtesting.state import BacktestResult
from alphalab.common.ids import id_scope, id_source
from alphalab.market.state import MarketState
from alphalab.runtime.execution_pipeline import ContextFactory, ExecutionPipelineResult
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine, RunState
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
# v2.3 and re-exported here unchanged. They moved so that a driver outside this
# package -- :mod:`alphalab.runtime.session` -- can mint reproducible
# identifiers without importing the backtesting engine, which imports it.
#
# Deriving an id source from a pipeline's *stream position* is a different
# thing and has exactly one implementation:
# :meth:`~alphalab.runtime.run.RunEngine.resume`. See ADR-0030.


def publish(market: MarketState, record: MarketRecord) -> MarketState:
    """Publish one dataset record to the market engine.

    Delegates to :meth:`~alphalab.runtime.run.RunEngine.publish_record`, which
    every environment publishes through.
    """

    return RunEngine.publish_record(market, record)


def initialize(config: RunConfig, strategy_state: StrategyRuntimeState) -> RunState:
    """Fund the portfolio and build the state a run starts from."""

    return RunEngine.initialize(config, strategy_state)


def advance(
    state: RunState,
    record: MarketRecord,
    context_factory: ContextFactory,
) -> tuple[RunState, ExecutionPipelineResult | None]:
    """Move one dataset record through the whole execution path.

    See :meth:`~alphalab.runtime.run.RunEngine.advance`. A dataset is validated
    chronological on construction and carries no wall clock, so no reading is
    passed and no record is ever stale.
    """

    return RunEngine.advance(state, record, context_factory)


def finalize(state: RunState) -> BacktestResult:
    """Compile analytics (if configured) and freeze the run into a result.

    The dataset the run consumed is *not* an argument. It is
    :attr:`~alphalab.runtime.run.RunState.source_id`, set by whichever driver
    started the run and carried by the run snapshot, so a run that stopped and
    continued in another process still knows what it was measured over. Until
    v2.14 this function took a ``dataset_id`` and a result could name a dataset
    the state had no record of -- one fact with two sources, which is how "what
    was this measured over?" becomes unanswerable. See ADR-0017 and ADR-0030.
    """

    return BacktestResult(run=RunEngine.finalize(state))


class BacktestEngine:
    """Runs a dataset through the canonical run step, deterministically."""

    @staticmethod
    def initialize(config: RunConfig, strategy_state: StrategyRuntimeState) -> RunState:
        """Fund the portfolio and build the state a run starts from."""

        return initialize(config, strategy_state)

    @staticmethod
    def resume(state: RunState) -> AbstractContextManager[None]:
        """The scope a restored run continues in.

        See :meth:`~alphalab.runtime.run.RunEngine.resume`, which is the only
        implementation of this contract in AlphaLab.
        """

        return RunEngine.resume(state)

    @staticmethod
    def advance(
        state: RunState,
        record: MarketRecord,
        context_factory: ContextFactory,
    ) -> tuple[RunState, ExecutionPipelineResult | None]:
        """Move one record through the path. See :func:`advance`."""

        return advance(state, record, context_factory)

    @staticmethod
    def finalize(state: RunState) -> BacktestResult:
        """Compile analytics and freeze the run. See :func:`finalize`."""

        return finalize(state)

    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
    ) -> BacktestResult:
        """Run ``dataset`` end to end and return the finished result.

        The run records the dataset it consumed as its ``source_id``, and
        declares :attr:`~alphalab.runtime.run.ExecutionMode.BACKTEST`.
        """

        with id_scope(config.seed):
            state = replace(
                initialize(replace(config, mode=ExecutionMode.BACKTEST), strategy_state),
                source_id=dataset.dataset_id,
            )
            for record in dataset.records:
                state, _ = advance(state, record, context_factory)
            return finalize(state)
