"""One loop, four environments.

A trading session reads market records from a
:class:`~alphalab.market.source.MarketDataSource` and moves each one through
:meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.process_record` --
the canonical step. That is the whole loop, and it is the same loop
:mod:`alphalab.backtesting` runs, because it is the same function.

The parity matrix
-----------------

============================ ========= ========= ========= =========
Layer                        Backtest  Replay    Paper     Live
============================ ========= ========= ========= =========
Market record                canonical canonical canonical canonical
Market event                 same      same      same      same
Strategy -> intents          same      same      same      same
Allocation -> OrderRequest   same      same      same      same
Risk -> RiskDecision         same      same      same      same
OMS order lifecycle          same      same      same      same
**Execution venue**          simulator simulator simulator **broker**
Fill                         canonical canonical canonical canonical
Portfolio accounting         same      same      same      same
Analytics                    same      same      same      same
---------------------------- --------- --------- --------- ---------
Record source                dataset   cursor    live      live
Clock                        record ts record ts wall      wall
Staleness gate               none      none      optional  optional
============================ ========= ========= ========= =========

Everything above the execution venue is one code path, not four that agree.
Backtest and replay differ from each other only in what drives the cursor
(:mod:`alphalab.backtesting.replay`). Paper differs from a backtest only in
where records come from and in the clock that judges them stale --- which is
why paper needs no accounting, order model or fill model of its own. Live
differs in one more thing: an accepted order is routed to a venue instead of
simulated, expressed by
:class:`~alphalab.runtime.execution_pipeline.ExecutionRouting`.

What is real, and what is a contract
------------------------------------

Backtest, replay and paper run end to end today. **Live does not**, and this
module does not pretend otherwise: a live session produces working orders and
stops, because AlphaLab contains no connectivity to any real venue.
:mod:`alphalab.runtime.broker_routing` implements and tests both directions of
the broker mapping, and :class:`~alphalab.broker.paper.PaperBroker` is the only
adapter that exists -- a simulation. Driving a real venue means supplying an
adapter, and the transport for it, from outside this repository.

Stale market data
-----------------

A backtest's records are all old and none of them are stale: staleness is age
measured against a clock that is *moving*, and a backtest's clock is the record
itself. Set ``max_market_data_age_seconds`` and pass the real clock to
:meth:`TradingSession.advance` when there is one; a record older than the limit
is skipped and recorded rather than acted on. Acting on a stale quote is how a
disconnected feed turns into real orders at prices that no longer exist.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import Enum, auto

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.ids import id_scope
from alphalab.execution.policy import FillPolicy, ImmediateFill
from alphalab.market.normalization import is_stale
from alphalab.market.record import MarketRecord
from alphalab.market.source import MarketDataSource
from alphalab.oms.order import Order as OMSOrder
from alphalab.runtime.execution_pipeline import (
    ContextFactory,
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineResult,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "ExecutionMode",
    "SessionConfig",
    "SessionState",
    "SkippedRecord",
    "TradingSession",
]


class ExecutionMode(Enum):
    """Which of the four environments a session is running."""

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
    """A record the session declined to act on, and why."""

    record: MarketRecord
    reason: str


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """What a session needs beyond the execution path's own configuration.

    Attributes:
        pipeline: Execution-path configuration threaded through every record.
        mode: Which environment this is.
        fill_policy: How a simulated venue answers each order. Ignored when
            ``mode`` routes externally, because then no fill is simulated.
        seed: Seed for the run's identifier stream. ``None`` leaves identifiers
            on ``uuid4``.
        start_timestamp: Instant the portfolio is funded, before any record.
        max_market_data_age_seconds: Oldest record the session will act on,
            measured against the clock passed to :meth:`TradingSession.advance`.
            ``None`` disables the gate, which is correct for historical runs.
    """

    pipeline: ExecutionPipelineConfig
    mode: ExecutionMode = ExecutionMode.PAPER
    fill_policy: FillPolicy = field(default_factory=ImmediateFill)
    seed: int | None = None
    start_timestamp: float = 0.0
    max_market_data_age_seconds: float | None = None

    def __post_init__(self) -> None:
        # The mode decides routing; a config that disagreed with its own mode
        # would execute one way and describe itself another.
        if self.pipeline.routing is not self.mode.routing:
            object.__setattr__(self, "pipeline", replace(self.pipeline, routing=self.mode.routing))


@dataclass(frozen=True, slots=True)
class SessionState:
    """Immutable snapshot of a session in progress.

    The session owns no accounting of its own: cash, positions, orders and
    fills all live on ``pipeline``. What this adds is the session's own
    bookkeeping -- how far it has read, and what it declined to act on.
    """

    config: SessionConfig
    pipeline: ExecutionPipelineState
    processed: int = 0
    current_timestamp: float = 0.0
    skipped: AppendOnlyLog[SkippedRecord] = field(default_factory=AppendOnlyLog)

    @property
    def working_orders(self) -> tuple[OMSOrder, ...]:
        """Orders still open in the OMS.

        In a live session these are the orders awaiting routing, or already
        routed and awaiting fills -- see :mod:`alphalab.runtime.broker_routing`.
        """

        return tuple(self.pipeline.oms.orders.open_orders())


class TradingSession:
    """Drives a market-data source through the canonical execution path."""

    @staticmethod
    def initialize(config: SessionConfig, strategy_state: StrategyRuntimeState) -> SessionState:
        """Fund the portfolio and build the state a session starts from."""

        return SessionState(
            config=config,
            pipeline=ExecutionPipeline.initialize(
                config.pipeline, strategy_state, config.start_timestamp
            ),
            current_timestamp=config.start_timestamp,
        )

    @staticmethod
    def advance(
        state: SessionState,
        record: MarketRecord,
        context_factory: ContextFactory,
        now: float | None = None,
    ) -> tuple[SessionState, ExecutionPipelineResult | None]:
        """Move one record through the execution path, unless it is too old.

        ``now`` is the session's clock. It defaults to the record's own
        timestamp, under which no record is ever stale -- the right answer for a
        historical run. A live session passes its real clock.

        Returns the next state and the pipeline result, or ``None`` when the
        record was skipped.
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

        result = ExecutionPipeline.process_record(
            state.pipeline, record, context_factory, state.config.fill_policy
        )
        return (
            replace(
                state,
                pipeline=result.state,
                processed=state.processed + 1,
                current_timestamp=record.timestamp,
            ),
            result,
        )

    @staticmethod
    def run(
        config: SessionConfig,
        source: MarketDataSource,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        clock: Iterable[float] | None = None,
    ) -> SessionState:
        """Read ``source`` to exhaustion through the execution path.

        ``clock`` supplies one reading per record for the staleness gate. Omit
        it and each record is judged against its own timestamp, which is what a
        historical source wants.
        """

        readings = iter(clock) if clock is not None else None
        with id_scope(config.seed):
            state = TradingSession.initialize(config, strategy_state)
            for record in source.records():
                now = next(readings, None) if readings is not None else None
                state, _ = TradingSession.advance(state, record, context_factory, now)
            return state
