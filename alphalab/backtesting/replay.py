"""Replay driven through the real execution path.

Before v2.2 :mod:`alphalab.replay` was a cursor and nothing more: it sequenced
historical events, tracked progress, and handed them to a caller that had to
build its own execution loop. Nothing in the execution path imported it, so
"deterministic replay" meant only that the same events came out in the same
order -- it said nothing about orders, fills or P&L, because replay never
produced any.

This module closes that gap without duplicating the loop. The replay engine
keeps doing exactly what it did -- own the cursor, the clock and the lifecycle
-- and every event it yields is handed to
:meth:`~alphalab.runtime.run.RunEngine.advance`, the same canonical step
:class:`~alphalab.backtesting.engine.BacktestEngine` and
:class:`~alphalab.runtime.session.TradingSession` take. Replay is therefore a
*driver* of the run runtime and not a runtime of its own -- it holds a cursor and
no execution state at all:

::

    historical dataset
      -> ReplayEngine.step_one_event      (cursor, clock, lifecycle)
      -> RunEngine.advance                (the canonical step)
           -> market -> strategy -> allocation -> risk
           -> OMS -> execution -> portfolio -> analytics

The replay clock is the record index, not wall time. A replay's own state is
then a pure function of its dataset, which is what lets two replays of one
dataset be compared field by field.

Pausing and resuming (v4.1, ledger FUT-001)
-------------------------------------------
Until v4.1 a replay could not stop and continue: the cursor had no snapshot and
drew from a second identifier stream nothing recorded, so an interrupted replay
restarted from its dataset (ADR-0029, ADR-0030). It now pauses at a boundary
*between* two records -- the boundary :meth:`RunEngine.resume
<alphalab.runtime.run.RunEngine.resume>` already places a run's continuation at
-- and resumes there, in this process or another:

::

    paused = ReplayBacktest.run(
        config,
        dataset,
        strategy_state,
        factory,
        pause_when=lambda progress: progress.records_replayed == 500,
    )
    payload = serialize(replay_snapshot.capture(paused))  # persist anywhere
    ...
    restored = replay_snapshot.restore(
        replay_snapshot.from_primitives(deserialize(payload)), objects, dataset
    )
    result = ReplayBacktest.resume(restored, factory)

``pause_when`` is asked at every boundary before a record is taken; a replay
whose last record has been taken has nothing left to pause before, and
finishes. :class:`PausedReplay` is the whole continuation: the run exactly as it
stood after its last record -- not finalized -- the cursor, ``PAUSED``, and where
the cursor's own stream had reached. A declared
:class:`~alphalab.runtime.retention.RetentionPolicy` bounds the cursor's
lifecycle log by its ``audit_events``, as it bounds every subsystem's audit
trail, so neither a long replay's memory nor its checkpoint grows with that log.
Both loops are :func:`_drive`, so a resumed
replay takes the same steps an uninterrupted one does, and its result -- every
order, fill, trade, position, cash balance, P&L figure and identifier of a
seeded run -- is byte for byte the uninterrupted result. The cursor's lifecycle
log says that it paused (``ReplayPaused``, ``ReplayResumed``); the run never
sees the cursor, so nothing it records can differ.

The cursor's envelope is :mod:`alphalab.backtesting.replay_snapshot`, outside
:class:`~alphalab.runtime.run.RunState` and
:data:`~alphalab.runtime.run_snapshot.RUN_SNAPSHOT_SCHEMA`, both of which are
unchanged: the replay envelope nests the run's.

External information and every other timed input (v4.1.1)
---------------------------------------------------------
Until v4.1.1 a replay delivered no observations: :meth:`BacktestEngine.run
<alphalab.backtesting.engine.BacktestEngine.run>` merged a
:class:`~alphalab.alt_data.streaming.DeliverySchedule` with its records (ledger
OFE-009) and :meth:`ReplayBacktest.run` took none, so a strategy acting on
news, a statement or a macro release produced one result as a backtest and
another as a replay of the same records. A replay now takes ``observations``
and a :class:`~alphalab.backtesting.schedule.RunSchedule` -- rates, funding,
cash flows, splits, timers, contract settlements -- applied by the same
:class:`~alphalab.backtesting.schedule.ScheduleCursor` the backtest uses, so the
two apply every input at the same step. A :class:`PausedReplay` carries the
schedule; what it has applied is a function of the last record it took, so a
resume -- in this process or, through :mod:`alphalab.backtesting.replay_snapshot`'s
schema 2, another -- continues with exactly what remained (ledger FUT-003).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any, overload

from alphalab.alt_data.streaming import ObservationDelivery
from alphalab.backtesting.algorithmic import AlgorithmBook, ExecutionPlan, algorithmic_run
from alphalab.backtesting.algorithmic import finish as finish_children
from alphalab.backtesting.algorithmic import work as work_children
from alphalab.backtesting.dataset import MarketDataset, MarketRecord
from alphalab.backtesting.engine import (
    finalize,
    id_scope,
    id_source,
    initialize,
)
from alphalab.backtesting.exceptions import UnsupportedRecordError
from alphalab.backtesting.schedule import EMPTY_SCHEDULE, RunSchedule, ScheduleCursor
from alphalab.backtesting.state import AlgorithmicResult, BacktestResult, ReplayResult
from alphalab.common.ids import IdStreamPosition, current_id_position, use_id_source
from alphalab.replay.engine import ReplayEngine
from alphalab.replay.exceptions import InvalidReplayStateError
from alphalab.replay.session import ReplaySession
from alphalab.replay.state import ReplayState, ReplayStatus
from alphalab.runtime.execution_pipeline import ContextFactory
from alphalab.runtime.retention import trimmed
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine, RunState
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "REPLAY_CURSOR_SEED_OFFSET",
    "PausedReplay",
    "ReplayBacktest",
    "ReplayProgress",
    "cursor_seed",
    "session_for",
]

#: Offset separating the replay cursor's identifier stream from the execution
#: path's. The cursor mints its own lifecycle event ids, and if it drew them
#: from the same stream the execution path uses, a replay's orders and fills
#: would carry different ids from the identical backtest's -- parity would fail
#: for a reason that has nothing to do with execution. Two streams from one
#: seed keep both reproducible and independent.
REPLAY_CURSOR_SEED_OFFSET = 0x5245504C  # "REPL"


def cursor_seed(seed: int | None) -> int | None:
    """The seed of the replay cursor's own identifier stream for a run seeded ``seed``.

    ``None`` -- an unseeded run -- leaves the cursor on ``uuid4`` as well.
    """

    return None if seed is None else seed + REPLAY_CURSOR_SEED_OFFSET


def session_for(dataset: MarketDataset, speed_multiplier: float = 1.0) -> ReplaySession:
    """Build the replay session that covers ``dataset`` exactly."""

    return ReplaySession(
        session_id=dataset.dataset_id,
        start_time=dataset.start_time,
        end_time=dataset.end_time,
        speed_multiplier=speed_multiplier,
    )


@dataclass(frozen=True, slots=True)
class ReplayProgress:
    """Where a replay stands at a boundary between two records.

    What ``pause_when`` is asked about, before the next record is taken.

    Attributes:
        records_replayed: Records the cursor has handed to the run so far.
        records_total: Records in the dataset.
        next_timestamp: The timestamp of the record that would be taken next.
    """

    records_replayed: int
    records_total: int
    next_timestamp: float

    @property
    def records_remaining(self) -> int:
        """Records the replay has not yet taken -- at least one at any boundary asked about."""

        return self.records_total - self.records_replayed


@dataclass(frozen=True, slots=True)
class PausedReplay:
    """A replay stopped at a boundary between two records (ledger FUT-001).

    Everything a continuation needs and nothing else, so capturing it, or
    resuming it, needs no other state. Immutable, like
    :class:`~alphalab.runtime.run.RunState` -- and, like it, holding the
    strategy *instances* the run calls, which are live objects: a strategy with
    memory of its own is advanced by a resume. To continue from one pause
    twice, restore each continuation from the captured payload, which
    carries every strategy's declared state (``StrategyStateProtocol``).

    Attributes:
        run: The run exactly as it stood after its last record. Not finalized:
            the instant of the last record may still be open, because the next
            record may share it.
        cursor: The replay cursor, ``PAUSED``. Its next record is the first one
            the run has not seen.
        cursor_position: Where the cursor's own identifier stream had reached
            (seed ``seed + REPLAY_CURSOR_SEED_OFFSET``), or an unseeded position
            for an unseeded run.
        schedule: Every timed input of the replay -- observations, rates,
            funding, cash flows, splits, timers, settlements (v4.1.1). What of it
            has been applied follows from the last record the cursor took
            (:meth:`~alphalab.backtesting.schedule.ScheduleCursor.start`), so it
            is held whole rather than as a remainder.
        algorithms: The execution plan's book, for a replay given one (v4.1.1,
            ledger FUT-009): every parent's algorithm as it stood at the pause,
            the children still working included; ``None`` otherwise.

    Raises:
        InvalidReplayStateError: If the three do not describe one replay paused
            between two records: the cursor is not paused, has no record left,
            names another dataset than the run, or has taken another number of
            records than the run processed or skipped.
    """

    run: RunState
    cursor: ReplayState
    cursor_position: IdStreamPosition
    schedule: RunSchedule = EMPTY_SCHEDULE
    algorithms: AlgorithmBook | None = None

    def __post_init__(self) -> None:
        cursor = self.cursor
        if cursor.status is not ReplayStatus.PAUSED:
            raise InvalidReplayStateError(
                f"A paused replay's cursor is PAUSED, not {cursor.status.name}."
            )
        if not 0 <= cursor.current_index < len(cursor.events):
            raise InvalidReplayStateError(
                f"A paused replay has a next record: its cursor is at {cursor.current_index} "
                f"of {len(cursor.events)}. A replay whose last record was taken finishes."
            )
        if self.run.config.mode is not ExecutionMode.REPLAY:
            raise InvalidReplayStateError(
                f"A paused replay's run is a REPLAY run, not {self.run.config.mode.name}."
            )
        if self.run.source_id != cursor.session.session_id:
            raise InvalidReplayStateError(
                f"The run read {self.run.source_id!r} and the cursor replays "
                f"{cursor.session.session_id!r}; a paused replay is one dataset's."
            )
        taken = self.run.processed + len(self.run.skipped) + self.run.skipped.dropped
        if taken != cursor.current_index:
            raise InvalidReplayStateError(
                f"The cursor has taken {cursor.current_index} records and the run has "
                f"processed or skipped {taken}; continuing would duplicate or omit "
                f"{abs(taken - cursor.current_index)}."
            )

    @property
    def records_replayed(self) -> int:
        """Records the replay took before it paused."""

        return self.cursor.current_index

    @property
    def records_total(self) -> int:
        """Records in the dataset the replay reads."""

        return len(self.cursor.events)

    @property
    def dataset_id(self) -> str | None:
        """The dataset the replay reads, read from the run as a result reads it."""

        return self.run.source_id


class ReplayBacktest:
    """Replays a dataset through the canonical execution path."""

    @staticmethod
    def initialize(dataset: MarketDataset) -> ReplayState:
        """Build a started replay cursor over ``dataset``.

        Real time starts at ``0.0`` and advances by one per record, so the
        cursor's own state carries no wall-clock reading and is reproducible.
        """

        state = ReplayEngine.initialize(session_for(dataset), dataset.records, 0.0)
        return ReplayEngine.start(state, 0.0)

    @overload
    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        *,
        observations: Iterable[ObservationDelivery[Any]] = (),
        schedule: RunSchedule = EMPTY_SCHEDULE,
        execution: ExecutionPlan | None = None,
    ) -> ReplayResult: ...

    @overload
    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        *,
        pause_when: Callable[[ReplayProgress], bool],
        observations: Iterable[ObservationDelivery[Any]] = (),
        schedule: RunSchedule = EMPTY_SCHEDULE,
        execution: ExecutionPlan | None = None,
    ) -> ReplayResult | PausedReplay: ...

    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        *,
        pause_when: Callable[[ReplayProgress], bool] | None = None,
        observations: Iterable[ObservationDelivery[Any]] = (),
        schedule: RunSchedule = EMPTY_SCHEDULE,
        execution: ExecutionPlan | None = None,
    ) -> ReplayResult | PausedReplay:
        """Replay ``dataset`` through the execution path, to its end or to a pause.

        The run records the dataset it consumed as its ``source_id``, and
        declares :attr:`~alphalab.runtime.run.ExecutionMode.REPLAY` -- which a
        replay could not say before v2.14, because it wrote a payload identical
        to a backtest's.

        ``pause_when`` (v4.1, ledger FUT-001) is asked at every boundary before
        a record is taken, the first included, and the replay pauses at the
        first boundary it answers true at, returning a :class:`PausedReplay`.
        It runs with ``uuid4`` installed, so nothing it mints draws from either
        of the replay's streams; an exception it raises propagates, and the
        replay is not paused. Without it, or once the last record has been
        taken, the replay finishes and returns its :class:`ReplayResult`.

        ``observations`` and ``schedule`` (v4.1.1) are applied exactly as
        :meth:`BacktestEngine.run <alphalab.backtesting.engine.BacktestEngine.run>`
        applies them -- each input at its instant, by
        :class:`~alphalab.backtesting.schedule.ScheduleCursor` -- so a replay of
        a strategy that acts on external information, settles a second
        currency, reacts to timers or holds a contract to its settlement equals
        the backtest of it.

        ``execution`` (v4.1.1, ledger FUT-009) works every order in children, as
        :meth:`BacktestEngine.run <alphalab.backtesting.engine.BacktestEngine.run>`
        does; a pause keeps the book, and a resume continues every algorithm.
        """

        config, book = algorithmic_run(config, execution, ExecutionMode.REPLAY)
        cursor_ids = id_source(cursor_seed(config.seed))

        with use_id_source(cursor_ids):
            replay = ReplayBacktest.initialize(dataset)

        with id_scope(config.seed):
            state = replace(
                initialize(replace(config, mode=ExecutionMode.REPLAY), strategy_state),
                source_id=dataset.dataset_id,
            )
            return _drive(
                state,
                replay,
                cursor_ids,
                context_factory,
                pause_when,
                schedule.with_observations(observations),
                book,
                resumed=False,
            )

    @overload
    @staticmethod
    def resume(paused: PausedReplay, context_factory: ContextFactory) -> ReplayResult: ...

    @overload
    @staticmethod
    def resume(
        paused: PausedReplay,
        context_factory: ContextFactory,
        *,
        pause_when: Callable[[ReplayProgress], bool],
    ) -> ReplayResult | PausedReplay: ...

    @staticmethod
    def resume(
        paused: PausedReplay,
        context_factory: ContextFactory,
        *,
        pause_when: Callable[[ReplayProgress], bool] | None = None,
    ) -> ReplayResult | PausedReplay:
        """Continue a paused replay from the record after the last one it took (ledger FUT-001).

        Unlike :meth:`BacktestEngine.resume
        <alphalab.backtesting.engine.BacktestEngine.resume>`, which opens the
        scope a caller then advances records in, this continues the replay
        itself, because the replay owns its loop: the run's stream is continued
        by :meth:`RunEngine.resume <alphalab.runtime.run.RunEngine.resume>` and
        the cursor's by :meth:`RunEngine.id_source_at
        <alphalab.runtime.run.RunEngine.id_source_at>`, each from the position
        it had reached, so no identifier is minted twice. The run then takes
        exactly the steps an uninterrupted replay takes from that record on.

        ``pause_when`` is asked as in :meth:`run`, from the boundary *after* the
        first record this call takes -- the replay is already paused at the one
        it resumes from -- so a resumed replay always moves.

        A paused replay from :func:`~alphalab.backtesting.replay_snapshot.restore`
        has been checked against the dataset and the live objects it is given;
        this does not check them again. Its schedule continues from the last
        record it took (:meth:`~alphalab.backtesting.schedule.ScheduleCursor.start`).
        """

        cursor_ids = RunEngine.id_source_at(paused.cursor_position)
        with RunEngine.resume(paused.run):
            with use_id_source(cursor_ids):
                replay = ReplayEngine.resume(paused.cursor, float(paused.cursor.current_index))
            return _drive(
                paused.run,
                replay,
                cursor_ids,
                context_factory,
                pause_when,
                paused.schedule,
                paused.algorithms,
                resumed=True,
            )


def _drive(
    state: RunState,
    replay: ReplayState,
    cursor_ids: Callable[[], str] | None,
    context_factory: ContextFactory,
    pause_when: Callable[[ReplayProgress], bool] | None,
    schedule: RunSchedule,
    book: AlgorithmBook | None,
    *,
    resumed: bool,
) -> ReplayResult | PausedReplay:
    """The replay loop -- the one :meth:`ReplayBacktest.run` and ``resume`` both take.

    Called inside the run's identifier scope. The cursor's stream is installed
    around each cursor transition only, so the two streams never mix.
    ``schedule`` is applied by the backtest's own cursor, record by record,
    started where the last record the cursor took leaves it; ``book`` works
    every parent at its asset's events, after the run's step at each, as the
    backtest does.
    """

    total = len(replay.events)
    replayed = replay.current_index
    retention = state.config.pipeline.retention.audit_events
    last: MarketRecord | None = None
    if replayed:
        previous = replay.events[replayed - 1]
        if not isinstance(previous, MarketRecord):
            raise UnsupportedRecordError(
                f"Replay holds {type(previous).__name__}, not a MarketRecord"
            )
        last = previous
    # What of the schedule the replay has applied follows from the last record
    # it took, processed or skipped -- see alphalab.backtesting.schedule.
    inputs = ScheduleCursor.start(schedule, None if last is None else last.timestamp)
    ask = not resumed

    while True:
        if (
            pause_when is not None
            and ask
            and replay.status is ReplayStatus.RUNNING
            and replayed < total
        ):
            progress = ReplayProgress(replayed, total, replay.events[replayed].timestamp)
            # Caller code: nothing it mints may come from the run's stream or
            # the cursor's.
            with use_id_source(None):
                pause = pause_when(progress)
            if pause:
                with use_id_source(cursor_ids):
                    paused = ReplayEngine.pause(replay, float(replayed))
                    position = current_id_position()
                return PausedReplay(state, paused, position, schedule, book)
        ask = True

        with use_id_source(cursor_ids):
            step = ReplayEngine.step_one_event(replay, float(replayed + 1))
        replay = step.state
        record = step.event
        if record is None:
            break
        if not isinstance(record, MarketRecord):
            # The cursor is typed to the historical-event protocol; a
            # dataset only ever puts MarketRecords into it, so anything
            # else means the cursor was fed from somewhere it should
            # not have been.
            raise UnsupportedRecordError(
                f"Replay yielded {type(record).__name__}, not a MarketRecord"
            )
        inputs, state = inputs.before(state, record.timestamp, context_factory)
        state = RunEngine.close_slice(state, context_factory, inputs.rates, before=record.timestamp)
        state, result = RunEngine.advance(state, record, context_factory, rates=inputs.rates)
        if book is not None and result is not None:
            book, state = work_children(book, state, result.market_event, inputs.rates)
        # The cursor's lifecycle log is an audit trail like any subsystem's, and
        # a declared retention bounds it as it bounds theirs (v4.1): until then
        # a replay kept one ReplayAdvanced per record whatever the run declared.
        kept = trimmed(replay.system_events, retention)
        if kept is not replay.system_events:
            replay = replace(replay, system_events=kept)
        last = record
        replayed += 1
    inputs, state = inputs.finish(state, context_factory, None if last is None else last.timestamp)
    state = RunEngine.close_slice(state, context_factory, inputs.rates)
    backtest: BacktestResult
    if book is None:
        backtest = finalize(state)
    else:
        book, state = finish_children(book, state)
        backtest = AlgorithmicResult(run=RunEngine.finalize(state), algorithms=book)

    return ReplayResult(
        backtest=backtest,
        replay_status=replay.status.name,
        records_replayed=replayed,
        last_record=last,
    )
