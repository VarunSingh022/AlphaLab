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

from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Any

from alphalab.alt_data.streaming import ObservationDelivery
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.arithmetic import in_accounting_context
from alphalab.common.evolve import evolve
from alphalab.common.ids import id_source_for, require_seed, use_id_source
from alphalab.core.fill import Fill as CoreFill
from alphalab.execution.policy import FillPolicy, ImmediateFill
from alphalab.execution.report import ExecutionReport
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.normalization import is_stale
from alphalab.market.record import MarketRecord
from alphalab.market.source import OrderingGuarantee
from alphalab.market.state import MarketState
from alphalab.oms.order import Order as OMSOrder
from alphalab.portfolio.corporate_actions import CashFlow, Split
from alphalab.portfolio.fx import NO_RATES, FxRates
from alphalab.runtime.assumptions import ExecutionAssumptions, execution_assumptions
from alphalab.runtime.exceptions import AlphaLabRuntimeError
from alphalab.runtime.execution_pipeline import (
    ContextFactory,
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineResult,
    ExecutionPipelineState,
    ExecutionRouting,
    UnpricedAsset,
    price_refusal,
    retained,
    wants_slices,
)
from alphalab.runtime.retention import trimmed
from alphalab.strategy.events import LifecycleTransitioned, ObservationReceived, TimerEvent
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
        years_elapsed: The span CAGR is compounded over, in years. ``None`` --
            the default as of v3.10 -- derives it from the equity curve's first
            and last instants. It defaulted to ``1.0``, so every run's CAGR and
            Calmar ratio were computed as if it had lasted exactly a year.
        risk_free_rate: Annual risk-free rate the Sharpe and Sortino ratios are
            in excess of. Recorded on the performance report.
        periods_per_year: How many return periods make a year, for
            annualization. ``None`` observes it from the curve (return periods
            divided by the years they span); the report records which.
        compile_analytics: Whether :meth:`RunEngine.finalize` compiles a
            performance report.
        halt_on_strategy_failure: Stop the run when a strategy fails: the
            record during which a strategy's hook raised, or it emitted an
            invalid intent, is processed and recorded, and then
            :meth:`RunEngine.advance` raises
            :class:`StrategyFailedError` carrying the
            run. ``False`` keeps running the strategies that did not fail, as
            every run before v3.10 did -- and either way the failure is on
            :attr:`RunState.strategy_failures` and the result (ledger EXE-006).
    """

    pipeline: ExecutionPipelineConfig
    mode: ExecutionMode
    fill_policy: FillPolicy = field(default_factory=ImmediateFill)
    seed: int | None = None
    start_timestamp: float = 0.0
    ordering: OrderingGuarantee = OrderingGuarantee.CHRONOLOGICAL
    max_market_data_age_seconds: float | None = None
    years_elapsed: float | None = None
    risk_free_rate: float = 0.0
    compile_analytics: bool = True
    periods_per_year: float | None = None
    halt_on_strategy_failure: bool = False

    def __post_init__(self) -> None:
        if self.seed is not None:
            require_seed(self.seed, "RunConfig.seed")
        # The mode decides routing; a config that disagreed with its own mode
        # would execute one way and describe itself another.
        if self.pipeline.routing is not self.mode.routing:
            object.__setattr__(self, "pipeline", evolve(self.pipeline, routing=self.mode.routing))

    @property
    def execution_assumptions(self) -> ExecutionAssumptions:
        """How this run models execution, and which of its assumptions are optimistic."""

        return execution_assumptions(self.pipeline, self.fill_policy)


@dataclass(frozen=True, slots=True)
class StrategyFailure:
    """One strategy that failed during a run, and why.

    Attributes:
        strategy_id: The strategy that failed.
        timestamp: The market instant it failed at.
        error: What its hook raised, or why its intent was refused.
    """

    strategy_id: str
    timestamp: float
    error: str


@dataclass(frozen=True, slots=True)
class RunState:
    """Immutable snapshot of a run in progress.

    The run owns no accounting of its own: cash, positions, orders, fills and
    the identifier stream position all live on ``pipeline``, which is the
    :class:`~alphalab.runtime.execution_pipeline.ExecutionPipelineState` every
    environment threads. What this adds is the run's own bookkeeping -- how far
    it has read, what it declined to act on, what each record produced, and
    which stream it read.

    Five of these nine fields are what a *continuation* needs: ``pipeline``,
    ``processed``, ``current_timestamp``, ``last_record_timestamp`` and -- since
    v3.11 -- ``last_slice_at``. The rest is provenance and observability,
    carried because ADR-0023's Class 1 includes it rather than because a
    decision reads it.
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
    #: The instant of the last slice this run closed, or ``None`` before the
    #: first (ledger EXE-004). A cursor, as ``last_record_timestamp`` is, and on
    #: the run rather than the pipeline for the same reason -- ADR-0030's
    #: performance budget, which the pipeline state spends eleven times a record.
    #: A slice is closed once: asked again for the same instant -- by a
    #: restarted live session, say -- the run does nothing, so no strategy
    #: decides twice on one instant. Set only when a strategy was there to
    #: receive the slice, which keeps a run without one exactly what it was.
    last_slice_at: float | None = None
    #: How many point-in-time records the run has delivered (ledger OFE-009).
    observations_delivered: int = 0
    #: ``(known_at, delivery_id)`` of the last record delivered, or ``None``: a
    #: cursor, so a delivery at or before it -- a restarted stream sending a
    #: record again -- is not delivered twice.
    last_observation: tuple[float, str] | None = None

    @property
    def working_orders(self) -> tuple[OMSOrder, ...]:
        """Orders still open in the OMS.

        In a live run these are the orders awaiting routing, or already routed
        and awaiting fills -- see :mod:`alphalab.runtime.broker_routing`.
        """

        return tuple(self.pipeline.oms.orders.open_orders())

    @property
    def strategy_failures(self) -> tuple[StrategyFailure, ...]:
        """Every strategy that failed during the run, in the order it failed.

        A strategy whose hook raises, or that emits an invalid intent, is moved
        to ``FAILED`` and dispatched nothing more; the run goes on with the
        others. Until v3.10 nothing on the run or its result said so, so a run
        whose only strategy crashed on its first record read as a successful
        run that chose not to trade (ledger EXE-006). Read from the strategy
        runtime's own lifecycle events, which the run snapshot carries.
        """

        return tuple(
            StrategyFailure(event.strategy_id, event.timestamp, event.reason)
            for event in self.pipeline.strategy.events
            if isinstance(event, LifecycleTransitioned) and event.new_state == "FAILED"
        )

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


class StrategyFailedError(AlphaLabRuntimeError):
    """A strategy failed and the run was configured to stop when one does.

    Raised by :meth:`~alphalab.runtime.run.RunEngine.advance` after the record
    during which a strategy's hook raised or emitted an invalid intent, when
    ``RunConfig.halt_on_strategy_failure`` is set (ledger EXE-006).

    Attributes:
        state: The run as it stood after that record, failure recorded -- what
            a caller inspects, or captures, to see what the run did up to it.
        failures: The failures that record produced.
    """

    def __init__(
        self, message: str, state: RunState, failures: tuple[StrategyFailure, ...]
    ) -> None:
        super().__init__(message)
        self.state = state
        self.failures = failures


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
    return evolve(state, skipped=state.skipped.append(SkippedRecord(record, detail))), None


def _retained(state: RunState) -> RunState:
    """``state`` with its derived histories held to the configured retention (PRF-004)."""

    policy = state.config.pipeline.retention
    if policy.keeps_everything:
        return state
    pipeline = retained(state.pipeline)
    steps = trimmed(state.steps, policy.steps)
    skipped = trimmed(state.skipped, policy.steps)
    if pipeline is state.pipeline and steps is state.steps and skipped is state.skipped:
        return state
    return evolve(state, pipeline=pipeline, steps=steps, skipped=skipped)


class RunEngine:
    """The canonical owner of a record-driven run."""

    @staticmethod
    @in_accounting_context
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
    @in_accounting_context
    def advance(
        state: RunState,
        record: MarketRecord,
        context_factory: ContextFactory,
        now: float | None = None,
        rates: FxRates = NO_RATES,
    ) -> tuple[RunState, ExecutionPipelineResult | None]:
        """Move one record through the execution path, and record what it did.

        The canonical run step, and the one every driver takes. In order:

        1. the staleness gate, judged against ``now``;
        2. the ordering gate, judged against ``last_record_timestamp``;
        3. the price gate (since v3.11): a price the instrument's declared
           economics do not admit -- a non-positive one, unless they allow
           negative prices -- is recorded as skipped
           (:func:`~alphalab.runtime.execution_pipeline.price_refusal`);
        4. :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.process_record`;
        5. a :class:`RunStep` recording what the record produced;
        6. the run cursor;
        7. since v3.12, the configured retention
           (:class:`~alphalab.runtime.retention.RetentionPolicy`): each derived
           history grown past its bound is trimmed back to it -- after the
           record, never during it (ledger PRF-004).

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
            return _retained(evolve(state, skipped=state.skipped.append(skipped))), None

        previous = state.last_record_timestamp
        if previous is not None and record.timestamp < previous:
            skipped_state, _ = _out_of_order(state, record, previous)
            return _retained(skipped_state), None

        refusal = price_refusal(state.pipeline, record.payload)
        if refusal is not None:
            # Recorded, not raised and not dropped: a price the instrument cannot
            # take is bad data, and a run says what it declined (ACC-007).
            return (
                _retained(
                    evolve(state, skipped=state.skipped.append(SkippedRecord(record, refusal)))
                ),
                None,
            )

        result = ExecutionPipeline.process_record(
            state.pipeline, record, context_factory, state.config.fill_policy, rates
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
        advanced = evolve(
            state,
            pipeline=result.state,
            processed=state.processed + 1,
            current_timestamp=record.timestamp,
            last_record_timestamp=record.timestamp,
            steps=state.steps.append(step),
        )
        if state.config.halt_on_strategy_failure and len(result.state.strategy.events) != len(
            state.pipeline.strategy.events
        ):
            before = len(state.strategy_failures)
            failures = advanced.strategy_failures[before:]
            if failures:
                named = ", ".join(f"{f.strategy_id} ({f.error})" for f in failures)
                raise StrategyFailedError(
                    f"The run stopped at record {state.processed} ({record.event_id}, "
                    f"t={record.timestamp}) because a strategy failed: {named}. "
                    "halt_on_strategy_failure is set; the error carries the run.",
                    state=advanced,
                    failures=failures,
                )
        return _retained(advanced), result

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
    @in_accounting_context
    def fire_timer(
        state: RunState,
        timer: TimerEvent,
        context_factory: ContextFactory,
        rates: FxRates = NO_RATES,
    ) -> RunState:
        """Deliver a timer the driver decided is due (ledger EXE-005).

        Strategies subscribed to ``timers`` receive it; what they ask for rests
        until each asset's next event. See
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.process_timer`.
        The run's clock moves to the timer's instant when that is later.
        """

        pipeline, _, _ = ExecutionPipeline.process_timer(
            state.pipeline, timer, context_factory, rates
        )
        return evolve(
            state,
            pipeline=pipeline,
            current_timestamp=max(state.current_timestamp, timer.timestamp),
        )

    @staticmethod
    @in_accounting_context
    def deliver_observation(
        state: RunState,
        delivery: ObservationDelivery[Any],
        context_factory: ContextFactory,
        rates: FxRates = NO_RATES,
        *,
        now: float | None = None,
    ) -> RunState:
        """Deliver one record of a schedule at the instant it became knowable (ledger OFE-009).

        Strategies subscribed to ``observations`` that define ``on_observation``
        receive an :class:`~alphalab.strategy.events.ObservationReceived`; what
        they ask for rests until each asset's next event. See
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.process_observation`.

        A historical driver delivers each record at its knowledge instant, and
        merges the schedule with its market records so that none is late
        (:meth:`~alphalab.backtesting.engine.BacktestEngine.run`). A live driver
        passes ``now``, its clock: a record arriving after it became knowable
        is delivered when it arrived, never earlier. A delivery at or before the
        run's cursor is a repeat and changes nothing.

        Raises:
            RuntimeValidationError: If the delivery instant is before the last
                event the run processed.
        """

        key = delivery.order_key
        if state.last_observation is not None and key <= state.last_observation:
            return state
        at = delivery.known_at if now is None else max(now, delivery.known_at)
        event = ObservationReceived(
            # Derived, not drawn from the run's identifier stream: delivering
            # information must not move every identifier minted after it.
            f"OBSERVATION-{delivery.delivery_id}",
            at,
            delivery_id=delivery.delivery_id,
            subject=delivery.record.subject,
            record=delivery.record,
        )
        pipeline, _, _ = ExecutionPipeline.process_observation(
            state.pipeline, event, context_factory, rates
        )
        return evolve(
            state,
            pipeline=pipeline,
            current_timestamp=max(state.current_timestamp, at),
            observations_delivered=state.observations_delivered + 1,
            last_observation=key,
        )

    @staticmethod
    @in_accounting_context
    def close_slice(
        state: RunState,
        context_factory: ContextFactory,
        rates: FxRates = NO_RATES,
        *,
        before: float | None = None,
    ) -> RunState:
        """Close the instant of the last record the run published (ledger EXE-004).

        Strategies subscribed to ``slices`` that define ``on_slice`` see the
        instant complete -- every price, every asset it was about -- and what
        they ask for rests until each asset's next event; see
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.close_slice`.

        Only the driver knows an instant is complete, so the driver calls this.
        ``before`` is how it does so without looking ahead: called with the
        timestamp of the record about to be advanced, the slice is closed only
        when the last published instant is earlier -- the first record of the
        next instant completes the previous one. Called without it, the last
        instant is closed as it stands, which is right at the end of the data
        and is the caller's judgement anywhere else. A slice is closed once;
        asking again for the same instant does nothing.
        """

        pipeline = state.pipeline
        events = pipeline.market.events
        if not len(events):
            return state
        at = events[-1].timestamp
        if before is not None and at >= before:
            return state
        if state.last_slice_at is not None and at <= state.last_slice_at:
            return state
        if not wants_slices(pipeline.strategy):
            return state
        closed, _, _ = ExecutionPipeline.close_slice(pipeline, context_factory, rates)
        return evolve(state, pipeline=closed, last_slice_at=at)

    @staticmethod
    @in_accounting_context
    def apply_cash_flow(
        state: RunState,
        flow: CashFlow,
        timestamp: float | None = None,
        rates: FxRates = NO_RATES,
    ) -> RunState:
        """Book a dividend, interest, a fee or a funding payment at ``timestamp`` (ACC-006).

        ``timestamp`` defaults to the run's current instant. See
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.apply_cash_flow`.
        The driver supplies cash flows as it supplies records: they are
        reference data, and which the run receives is its business.
        """

        clock = state.current_timestamp if timestamp is None else timestamp
        pipeline = ExecutionPipeline.apply_cash_flow(state.pipeline, flow, clock, rates)
        return evolve(
            state, pipeline=pipeline, current_timestamp=max(state.current_timestamp, clock)
        )

    @staticmethod
    @in_accounting_context
    def apply_split(
        state: RunState,
        split: Split,
        timestamp: float | None = None,
        rates: FxRates = NO_RATES,
    ) -> RunState:
        """Apply a split, reverse split or stock dividend at ``timestamp`` (ACC-006).

        ``timestamp`` defaults to the run's current instant. See
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.apply_split`.
        """

        clock = state.current_timestamp if timestamp is None else timestamp
        pipeline = ExecutionPipeline.apply_split(state.pipeline, split, clock, rates)
        return evolve(
            state, pipeline=pipeline, current_timestamp=max(state.current_timestamp, clock)
        )

    @staticmethod
    @in_accounting_context
    def stop(
        state: RunState,
        context_factory: ContextFactory,
        now: float | None = None,
        rates: FxRates = NO_RATES,
        strategy_ids: Iterable[str] | None = None,
    ) -> RunState:
        """Stop the run's strategies: ``on_shutdown``, then ``on_stop``, then ``STOPPED``.

        The one place a run delivers the two hooks (ledger EXE-005), and a
        deliberate act rather than a side effect of :meth:`finalize`: a backtest
        that finalizes one segment of its data may be continued with the next,
        and a stopped strategy is dispatched nothing more. ``now`` is the instant
        the strategies are stopped, defaulting to the run's current timestamp.
        Shutdown orders rest until their asset's next event -- see
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.stop_strategies`.
        """

        clock = state.current_timestamp if now is None else now
        pipeline, _, _ = ExecutionPipeline.stop_strategies(
            state.pipeline, context_factory, clock, rates, strategy_ids
        )
        return evolve(state, pipeline=pipeline)

    @staticmethod
    @in_accounting_context
    def finalize(state: RunState) -> RunState:
        """Compile analytics, if the run is configured to, and return the run.

        Separate from any driver's result type: a driver turns a finished
        :class:`RunState` into whatever it reports, and this is the last thing
        the runtime itself does to one.
        """

        if not state.config.compile_analytics:
            return state

        return evolve(
            state,
            pipeline=ExecutionPipeline.compile_analytics(
                state.pipeline,
                state.current_timestamp,
                state.config.years_elapsed,
                state.config.risk_free_rate,
                state.config.periods_per_year,
            ),
        )
