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

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Any, Protocol, overload

from alphalab.alt_data.streaming import ObservationDelivery
from alphalab.backtesting.algorithmic import ExecutionPlan, algorithmic_run
from alphalab.backtesting.algorithmic import _work as work_children
from alphalab.backtesting.algorithmic import finish as finish_children
from alphalab.backtesting.dataset import MarketDataset, MarketRecord
from alphalab.backtesting.queueing import _planned_result, queued_result, queued_run
from alphalab.backtesting.schedule import EMPTY_SCHEDULE, RunSchedule, ScheduleCursor
from alphalab.backtesting.schedule import _before as schedule_before
from alphalab.backtesting.schedule import _finish as schedule_finish
from alphalab.backtesting.state import (
    AlgorithmicQueuedResult,
    AlgorithmicResult,
    BacktestResult,
    QueuedResult,
)
from alphalab.common.evolve import evolve
from alphalab.common.ids import id_scope, id_source
from alphalab.execution.queue import QueueModel
from alphalab.market.state import MarketState
from alphalab.portfolio.fx import FxRates
from alphalab.runtime.execution_pipeline import ContextFactory, ExecutionPipelineResult
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine, RunState
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "BacktestEngine",
    "advance",
    "close_slice",
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


def close_slice(
    state: RunState,
    context_factory: ContextFactory,
    *,
    before: float | None = None,
) -> RunState:
    """Close the instant of the last record -- when ``before`` shows it complete.

    See :meth:`~alphalab.runtime.run.RunEngine.close_slice` (ledger EXE-004). A
    caller stepping a dataset by hand calls this with each record's timestamp
    before advancing it, and once without one at the end of the data, as
    :meth:`BacktestEngine.run` does.
    """

    return RunEngine.close_slice(state, context_factory, before=before)


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
    def close_slice(
        state: RunState,
        context_factory: ContextFactory,
        *,
        before: float | None = None,
    ) -> RunState:
        """Close the last record's instant when complete. See :func:`close_slice`."""

        return close_slice(state, context_factory, before=before)

    @staticmethod
    def finalize(state: RunState) -> BacktestResult:
        """Compile analytics and freeze the run. See :func:`finalize`."""

        return finalize(state)

    @overload
    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        observations: Iterable[ObservationDelivery[Any]] = (),
        *,
        schedule: RunSchedule = EMPTY_SCHEDULE,
        execution: None = None,
        queue: None = None,
    ) -> BacktestResult: ...

    @overload
    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        observations: Iterable[ObservationDelivery[Any]] = (),
        *,
        schedule: RunSchedule = EMPTY_SCHEDULE,
        execution: ExecutionPlan,
        queue: None = None,
    ) -> AlgorithmicResult: ...

    @overload
    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        observations: Iterable[ObservationDelivery[Any]] = (),
        *,
        schedule: RunSchedule = EMPTY_SCHEDULE,
        execution: None = None,
        queue: QueueModel,
    ) -> QueuedResult: ...

    @overload
    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        observations: Iterable[ObservationDelivery[Any]] = (),
        *,
        schedule: RunSchedule = EMPTY_SCHEDULE,
        execution: ExecutionPlan,
        queue: QueueModel,
    ) -> AlgorithmicQueuedResult: ...

    @overload
    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        observations: Iterable[ObservationDelivery[Any]] = (),
        *,
        schedule: RunSchedule = EMPTY_SCHEDULE,
        execution: ExecutionPlan | None,
        queue: QueueModel | None = None,
    ) -> BacktestResult: ...

    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        observations: Iterable[ObservationDelivery[Any]] = (),
        *,
        schedule: RunSchedule = EMPTY_SCHEDULE,
        execution: ExecutionPlan | None = None,
        queue: QueueModel | None = None,
    ) -> BacktestResult:
        """Run ``dataset`` end to end and return the finished result.

        The run records the dataset it consumed as its ``source_id``, and
        declares :attr:`~alphalab.runtime.run.ExecutionMode.BACKTEST`. Each
        instant's slice is closed when the next instant's first record arrives,
        and the last when the data ends (ledger EXE-004).

        ``observations`` -- a :class:`~alphalab.alt_data.streaming.DeliverySchedule`,
        or any deliveries -- are merged with the records by instant (ledger
        OFE-009): each is delivered at its knowledge instant, **after** every
        record of that instant and before its slice closes, so what a strategy
        does with information known at ``t`` rests until a price printed after
        the instant's own -- and an ``on_slice`` at ``t`` sees it. One known
        after the last record is beyond the data, and is not delivered.

        ``schedule`` (v4.1.1, ledger FUT-004) carries every other timed input --
        exchange rates, funding, cash flows, splits, timers, contract settlements
        -- applied by :class:`~alphalab.backtesting.schedule.ScheduleCursor` at
        its instant, the rate table it builds converting every step; the
        ``observations`` given here are added to it.

        ``execution`` (v4.1.1, ledger FUT-009) works every order in children:
        the run routes
        :attr:`~alphalab.runtime.execution_pipeline.ExecutionRouting.ALGORITHMIC`,
        and the plan's algorithms release, trade and finish each parent at its
        asset's events, after the run's own step at each -- see
        :mod:`alphalab.backtesting.algorithmic`. The result is then an
        :class:`~alphalab.backtesting.state.AlgorithmicResult`, carrying the book.

        ``queue`` (v4.1.1, ledger FUT-011) is a queue model: each resting limit
        order keeps its place at its price level and fills only as far as the
        model says has reached the front -- see
        :mod:`alphalab.backtesting.queueing`. The result is then a
        :class:`~alphalab.backtesting.state.QueuedResult`, whose execution
        assumptions name the model. Beside ``execution`` (v4.3, ADR-0061) it
        governs the plan's resting limit children, and the result is an
        :class:`~alphalab.backtesting.state.AlgorithmicQueuedResult` holding both
        books.
        """

        return _drive(
            config,
            dataset,
            strategy_state,
            context_factory,
            observations,
            schedule,
            execution,
            queue,
            None,
        )


class _RunObserver(Protocol):
    """What the step loop tells an observer of its run (v4.3, ADR-0055).

    An observer reads and changes nothing: the loop hands it each state and goes
    on from that same state, so a run takes the same steps with one or without.
    """

    def seen(self, state: RunState, rates: FxRates) -> None:
        """After each operation: the state it produced, and the rates it valued the book at."""

    def step_ended(self) -> None:
        """After each step: the run's initialization, each record, the end of the data."""


def _drive(
    config: RunConfig,
    dataset: MarketDataset,
    strategy_state: StrategyRuntimeState,
    context_factory: ContextFactory,
    observations: Iterable[ObservationDelivery[Any]],
    schedule: RunSchedule,
    execution: ExecutionPlan | None,
    queue: QueueModel | None,
    observer: _RunObserver | None,
) -> BacktestResult:
    """The one step loop :meth:`BacktestEngine.run` and a captured run drive (ADR-0055).

    ``observer``, when given, is told the state after every operation the loop
    performs -- each schedule entry and each slice the cursor closes, the
    record's own slice close, the record, the algorithms' work -- and the end of
    every step. Each operation books what it books before the equity point it
    records, so what an observer reads after one is never another's.
    """

    observe = None if observer is None else observer.seen
    config, book = algorithmic_run(config, execution, ExecutionMode.BACKTEST)
    queued = queued_run(config, queue)
    cursor = ScheduleCursor.start(schedule.with_observations(observations))
    with id_scope(config.seed):
        state = evolve(
            initialize(evolve(config, mode=ExecutionMode.BACKTEST), strategy_state),
            source_id=dataset.dataset_id,
        )
        if observer is not None:
            observer.seen(state, cursor.rates)
            observer.step_ended()
        for record in dataset.records:
            # Everything ordered before this record's instant, in order; an
            # instant's slice closes before anything later than it.
            cursor, state = schedule_before(
                cursor, state, record.timestamp, context_factory, observe
            )
            # The first record of an instant completes the one before it.
            state = RunEngine.close_slice(
                state, context_factory, cursor.rates, before=record.timestamp
            )
            if observe is not None:
                observe(state, cursor.rates)
            # Under a plan the run's own orders are parents, which never rest: the
            # queue book is the children's, worked beside them (ADR-0061).
            state, result = RunEngine.advance(
                state,
                record,
                context_factory,
                rates=cursor.rates,
                queue=queued if book is None else None,
            )
            if observe is not None:
                observe(state, cursor.rates)
            if result is not None and result.queue is not None:
                queued = result.queue
            if book is not None and result is not None:
                book, state, queued = work_children(
                    book, state, result.market_event, cursor.rates, queued, context_factory
                )
                if observe is not None:
                    observe(state, cursor.rates)
            if observer is not None:
                observer.step_ended()
        cursor, state = schedule_finish(
            cursor, state, context_factory, dataset.records[-1].timestamp, observe
        )
        state = RunEngine.close_slice(state, context_factory, cursor.rates)
        if observe is not None:
            observe(state, cursor.rates)
        if book is not None:
            book, state = finish_children(book, state)
            if observe is not None:
                observe(state, cursor.rates)
        if observer is not None:
            observer.step_ended()
        if book is not None and queued is not None:
            return _planned_result(state, book, queued)
        if queued is not None:
            return queued_result(state, queued)
        if book is None:
            return finalize(state)
        return AlgorithmicResult(run=RunEngine.finalize(state), algorithms=book)
