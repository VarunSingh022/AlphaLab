"""The run runtime: one owner for a record-driven run, and the drivers around it.

Two tiers, and this module is the second of them.
:class:`~alphalab.runtime.execution_pipeline.ExecutionPipeline` owns the
*execution step* -- market, strategy, allocation, risk, OMS, execution,
portfolio, analytics -- and is unchanged by this module.
:class:`RunEngine` owns the *run*: how far it has read, what it declined to act
on, what each record produced, and the scope a stopped run continues in.

Why this exists
---------------
Until v2.14 the run layer was implemented twice.
``TradingSession.resume`` and ``BacktestEngine.resume`` were
character-identical::

    return use_id_source(id_source_for(state.pipeline.id_position))

Two owners of one determinism contract is a contract that can drift, and
ADR-0023 decision 1 had already recorded that this layer was the one a future
integrated-runtime release would reshape. ADR-0030 is that reshape.
:meth:`RunEngine.resume` is now the only definition in the repository that
derives an identifier source from a pipeline's stream position.

What a driver is
----------------
Anything that decides **which record comes next** and **what clock reading to
judge it by**, and that shapes a finished :class:`RunState` into its own result
type. A driver holds no execution state and no run state of its own:

======================================================  ==========================
:class:`~alphalab.runtime.session.TradingSession`       a ``MarketDataSource``
:class:`~alphalab.backtesting.engine.BacktestEngine`    a ``MarketDataset``
:class:`~alphalab.backtesting.replay.ReplayBacktest`    ``alphalab.replay``'s cursor
======================================================  ==========================

A streaming driver and a live driver are later additions to that table and
require no change here -- which is the whole point of putting the clock in a
parameter rather than in the state. See ADR-0030.

The clock
---------
Nothing on the execution path reads a wall clock; every timestamp comes from
``event.timestamp``, ``record.timestamp`` or ``report.timestamp``. The one clock
in a run is the ``now`` argument to :meth:`RunEngine.advance`, supplied per step
by the driver and never stored. It decides exactly one thing: whether a record
is too old to act on. A historical run passes nothing and each record is judged
against its own timestamp, under which no record is ever stale.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum, auto

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.ids import id_source_for, use_id_source
from alphalab.core.fill import Fill as CoreFill
from alphalab.execution.policy import FillPolicy, ImmediateFill
from alphalab.execution.report import ExecutionReport
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.normalization import is_stale
from alphalab.market.record import MarketRecord
from alphalab.market.source import OrderingGuarantee
from alphalab.market.state import MarketState
from alphalab.oms.order import Order as OMSOrder
from alphalab.runtime.execution_pipeline import (
    ContextFactory,
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineResult,
    ExecutionPipelineState,
    ExecutionRouting,
    UnpricedAsset,
)
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "ExecutionMode",
    "RunConfig",
    "RunEngine",
    "RunState",
    "RunStep",
    "SkippedRecord",
]


class ExecutionMode(Enum):
    """Which of the four environments a run is in.

    Every driver declares one. Until v2.14 ``BACKTEST`` and ``REPLAY`` were set
    by nothing in the package -- only a session carried a mode, so the
    environment a run was in was stated in one driver and defaulted in the
    others. A captured run can now say what it was.
    """

    BACKTEST = auto()
    REPLAY = auto()
    PAPER = auto()
    LIVE = auto()

    @property
    def routing(self) -> ExecutionRouting:
        """Where an accepted order executes in this mode."""

        return (
            ExecutionRouting.EXTERNAL if self is ExecutionMode.LIVE else ExecutionRouting.SIMULATED
        )

    @property
    def is_realtime(self) -> bool:
        """Whether records arrive against a moving wall clock.

        The only thing this changes is whether staleness is a meaningful
        question. It does not change how anything executes.
        """

        return self in {ExecutionMode.PAPER, ExecutionMode.LIVE}


@dataclass(frozen=True, slots=True)
class SkippedRecord:
    """A record the run declined to act on, and why."""

    record: MarketRecord
    reason: str


@dataclass(frozen=True, slots=True)
class RunStep:
    """What one record produced when it went through the path.

    Every field is derived from the record and the
    :class:`~alphalab.runtime.execution_pipeline.ExecutionPipelineResult` it
    produced, and no decision reads one. It is observability, and it belongs to
    the run rather than to any one driver: this was ``BacktestStep`` until v2.14
    only because the backtest was the first driver to want a per-record log.
    """

    index: int
    event_id: str
    timestamp: float
    orders: tuple[OMSOrder, ...]
    reports: tuple[ExecutionReport, ...]
    fills: tuple[CoreFill, ...]
    equity: Decimal


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Everything a run needs beyond the execution path's own configuration.

    The union of what ``SessionConfig`` and ``BacktestConfig`` carried, and not
    a merger of two things that looked alike: every field here is read by a
    run-level function :class:`RunEngine` owns. ``ordering`` and
    ``max_market_data_age_seconds`` are read by :meth:`RunEngine.advance`;
    ``years_elapsed``, ``risk_free_rate`` and ``compile_analytics`` by
    :meth:`RunEngine.finalize`. That only one of the two old drivers exposed
    each of them was an accident of which driver was written first -- a session
    that wants a performance report needs exactly the three ``finalize`` reads.

    Attributes:
        pipeline: Execution-path configuration threaded through every record.
            The same object as ``RunState.pipeline.config``, so the snapshot
            carries it once.
        mode: Which environment this run is in. Required: a run that cannot say
            which environment it is in cannot say what its captured state means.
        fill_policy: How a simulated venue answers each order. Ignored when
            ``mode`` routes externally, because then no fill is simulated.
        seed: Seed for the run's identifier stream. ``None`` leaves identifiers
            on ``uuid4``.
        start_timestamp: Instant the portfolio is funded, before any record.
        ordering: What this run will accept from its records. The default
            requires timestamps never to go backwards, and a record that
            regresses raises. Set it to ``UNORDERED`` to have such a record
            skipped and recorded instead. See ADR-0014.
        max_market_data_age_seconds: Oldest record the run will act on, measured
            against the clock passed to :meth:`RunEngine.advance`. ``None``
            disables the gate, which is correct for historical runs.
        years_elapsed: Period length handed to the analytics engine for
            annualised figures.
        risk_free_rate: Risk-free rate handed to the analytics engine.
        compile_analytics: Whether :meth:`RunEngine.finalize` compiles a
            performance report.
    """

    pipeline: ExecutionPipelineConfig
    mode: ExecutionMode
    fill_policy: FillPolicy = field(default_factory=ImmediateFill)
    seed: int | None = None
    start_timestamp: float = 0.0
    ordering: OrderingGuarantee = OrderingGuarantee.CHRONOLOGICAL
    max_market_data_age_seconds: float | None = None
    years_elapsed: float = 1.0
    risk_free_rate: float = 0.0
    compile_analytics: bool = True

    def __post_init__(self) -> None:
        # The mode decides routing; a config that disagreed with its own mode
        # would execute one way and describe itself another.
        if self.pipeline.routing is not self.mode.routing:
            object.__setattr__(self, "pipeline", replace(self.pipeline, routing=self.mode.routing))


@dataclass(frozen=True, slots=True)
class RunState:
    """Immutable snapshot of a run in progress.

    The run owns no accounting of its own: cash, positions, orders, fills and
    the identifier stream position all live on ``pipeline``, which is the
    :class:`~alphalab.runtime.execution_pipeline.ExecutionPipelineState` every
    environment threads. What this adds is the run's own bookkeeping -- how far
    it has read, what it declined to act on, what each record produced, and
    which stream it read.

    Four of these eight fields are what a *continuation* needs: ``pipeline``,
    ``processed``, ``current_timestamp`` and ``last_record_timestamp``. The rest
    is provenance and observability, carried because ADR-0023's Class 1 includes
    it rather than because a decision reads it.
    """

    config: RunConfig
    pipeline: ExecutionPipelineState
    processed: int = 0
    current_timestamp: float = 0.0
    #: Timestamp of the newest record actually processed, or ``None`` before the
    #: first one. Kept apart from ``current_timestamp``, which starts at the
    #: funding instant, so the ordering check only ever compares record to
    #: record.
    last_record_timestamp: float | None = None
    #: The stream this run read: a source's ``source_id``, a dataset's
    #: ``dataset_id``, or ``None`` when the run was driven record by record and
    #: named nothing. **One home for one fact.** Until v2.14 a session kept its
    #: ``source_id`` on the state and captured it, while a backtest's
    #: ``dataset_id`` was an argument to ``finalize`` that no snapshot carried --
    #: so a backtest that stopped and continued in another process lost the
    #: identity its evidence depends on. ``None`` is an honest absence and not a
    #: default identity; see ADR-0017 and ADR-0030.
    source_id: str | None = None
    steps: AppendOnlyLog[RunStep] = field(default_factory=AppendOnlyLog)
    skipped: AppendOnlyLog[SkippedRecord] = field(default_factory=AppendOnlyLog)

    @property
    def working_orders(self) -> tuple[OMSOrder, ...]:
        """Orders still open in the OMS.

        In a live run these are the orders awaiting routing, or already routed
        and awaiting fills -- see :mod:`alphalab.runtime.broker_routing`.
        """

        return tuple(self.pipeline.oms.orders.open_orders())

    @property
    def unpriced_assets(self) -> tuple[UnpricedAsset, ...]:
        """Assets this run declined to trade for want of a price.

        Read from the pipeline rather than stored again, so a run and its
        execution state cannot disagree. Distinct from :attr:`skipped`, which is
        about *records* the run declined to process at all: an unpriced asset's
        records were processed normally, and it is the strategy's order for
        something the run never priced that was dropped.
        """

        return tuple(self.pipeline.unpriced_assets.values())


def _out_of_order(
    state: RunState, record: MarketRecord, previous: float
) -> tuple[RunState, ExecutionPipelineResult | None]:
    """Answer a record whose timestamp went backwards.

    The market engine writes ``latest_quotes[asset_id]`` unconditionally and the
    pipeline marks the portfolio to whatever it finds there, so acting on an
    older record rewrites valuation backwards and nothing downstream notices.
    Neither answer here is a reorder: the record is refused or it is dropped,
    and nothing is buffered or held back.
    """

    detail = (
        f"Record {record.event_id!r} is timestamped {record.timestamp}, which is "
        f"before the last record processed at {previous}."
    )
    if state.config.ordering is OrderingGuarantee.CHRONOLOGICAL:
        raise MarketValidationError(
            f"{detail} This run requires chronological records, so its source "
            "broke the guarantee it declared. Set RunConfig.ordering to "
            "UNORDERED to skip such records instead."
        )
    return replace(state, skipped=state.skipped.append(SkippedRecord(record, detail))), None


class RunEngine:
    """The canonical owner of a record-driven run."""

    @staticmethod
    def initialize(config: RunConfig, strategy_state: StrategyRuntimeState) -> RunState:
        """Fund the portfolio and build the state a run starts from."""

        return RunState(
            config=config,
            pipeline=ExecutionPipeline.initialize(
                config.pipeline, strategy_state, config.start_timestamp
            ),
            current_timestamp=config.start_timestamp,
        )

    @staticmethod
    def publish_record(market: MarketState, record: MarketRecord) -> MarketState:
        """Publish one market record to the market engine.

        Delegates to
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.publish_record`,
        which every environment publishes through.
        """

        return ExecutionPipeline.publish_record(market, record)

    @staticmethod
    def advance(
        state: RunState,
        record: MarketRecord,
        context_factory: ContextFactory,
        now: float | None = None,
    ) -> tuple[RunState, ExecutionPipelineResult | None]:
        """Move one record through the execution path, and record what it did.

        The canonical run step, and the one every driver takes. In order:

        1. the staleness gate, judged against ``now``;
        2. the ordering gate, judged against ``last_record_timestamp``;
        3. :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.process_record`;
        4. a :class:`RunStep` recording what the record produced;
        5. the run cursor.

        ``now`` is the run's clock. It defaults to the record's own timestamp,
        under which no record is ever stale -- the right answer for a historical
        run. A live or paper run passes its real clock.

        Returns the next state and the pipeline result, or ``None`` when the
        record was skipped -- because it was stale, or because its timestamp went
        backwards and the run tolerates that.

        Raises:
            MarketValidationError: If the record's timestamp regresses and
                ``config.ordering`` is ``CHRONOLOGICAL``. Acting on it would mark
                the portfolio at a price the market has already moved past, and
                the market engine's ``latest_*`` index would silently take the
                older quote as current. See ADR-0014.
        """

        clock = record.timestamp if now is None else now
        limit = state.config.max_market_data_age_seconds
        if limit is not None and is_stale(record.timestamp, clock, limit):
            skipped = SkippedRecord(
                record,
                f"Market data timestamped {record.timestamp} is older than the "
                f"{limit}s limit at {clock}.",
            )
            return replace(state, skipped=state.skipped.append(skipped)), None

        previous = state.last_record_timestamp
        if previous is not None and record.timestamp < previous:
            return _out_of_order(state, record, previous)

        result = ExecutionPipeline.process_record(
            state.pipeline, record, context_factory, state.config.fill_policy
        )
        step = RunStep(
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
                last_record_timestamp=record.timestamp,
                steps=state.steps.append(step),
            ),
            result,
        )

    @staticmethod
    def resume(state: RunState) -> AbstractContextManager[None]:
        """The scope a restored run continues in.

        **The only definition in AlphaLab that derives an identifier source from
        a pipeline's stream position.** Until v2.14 there were two, one per
        driver, and they were character-identical -- see this module's docstring
        and ADR-0030.

        The counterpart of the ``id_scope`` a driver's ``run`` opens: ``run``
        starts a stream from a seed, a resumed run continues one, so the source
        is rebuilt from the position the restored pipeline carries rather than
        from the seed alone. That is what stops a continued run re-minting
        identifiers it has already used. An unseeded run resumes on ``uuid4``,
        exactly as it ran.

        Restoring state and continuing execution stay separate:
        :func:`alphalab.runtime.run_snapshot.restore` installs nothing, and this
        installs nothing but the source. Advance the records the run has not seen
        inside the block::

            with RunEngine.resume(restored):
                for record in remaining:
                    restored, _ = RunEngine.advance(restored, record, factory)

        The boundary is between :meth:`advance` calls. A run that processed N
        records resumes at record N+1; nothing is replayed.
        """

        return use_id_source(id_source_for(state.pipeline.id_position))

    @staticmethod
    def finalize(state: RunState) -> RunState:
        """Compile analytics, if the run is configured to, and return the run.

        Separate from any driver's result type: a driver turns a finished
        :class:`RunState` into whatever it reports, and this is the last thing
        the runtime itself does to one.
        """

        if not state.config.compile_analytics:
            return state

        return replace(
            state,
            pipeline=ExecutionPipeline.compile_analytics(
                state.pipeline,
                state.current_timestamp,
                state.config.years_elapsed,
                state.config.risk_free_rate,
            ),
        )
