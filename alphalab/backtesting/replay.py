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

**Replay is still not resumable**, and v2.14 does not change that: the cursor has
no snapshot and draws from a second identifier stream. Closing that needs a
cursor snapshot and a second :class:`~alphalab.common.ids.IdStreamPosition`, both
of which are additive to this driver and reach neither
:class:`~alphalab.runtime.run.RunState` nor
:data:`~alphalab.runtime.run_snapshot.RUN_SNAPSHOT_SCHEMA`. See ADR-0030.
"""

from __future__ import annotations

from dataclasses import replace

from alphalab.backtesting.dataset import MarketDataset, MarketRecord
from alphalab.backtesting.engine import advance, finalize, id_scope, id_source, initialize
from alphalab.backtesting.exceptions import UnsupportedRecordError
from alphalab.backtesting.state import ReplayResult
from alphalab.common.ids import use_id_source
from alphalab.replay.engine import ReplayEngine
from alphalab.replay.session import ReplaySession
from alphalab.replay.state import ReplayState
from alphalab.runtime.execution_pipeline import ContextFactory
from alphalab.runtime.run import ExecutionMode, RunConfig
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = ["REPLAY_CURSOR_SEED_OFFSET", "ReplayBacktest", "session_for"]

#: Offset separating the replay cursor's identifier stream from the execution
#: path's. The cursor mints its own lifecycle event ids, and if it drew them
#: from the same stream the execution path uses, a replay's orders and fills
#: would carry different ids from the identical backtest's -- parity would fail
#: for a reason that has nothing to do with execution. Two streams from one
#: seed keep both reproducible and independent.
REPLAY_CURSOR_SEED_OFFSET = 0x5245504C  # "REPL"


def session_for(dataset: MarketDataset, speed_multiplier: float = 1.0) -> ReplaySession:
    """Build the replay session that covers ``dataset`` exactly."""

    return ReplaySession(
        session_id=dataset.dataset_id,
        start_time=dataset.start_time,
        end_time=dataset.end_time,
        speed_multiplier=speed_multiplier,
    )


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

    @staticmethod
    def run(
        config: RunConfig,
        dataset: MarketDataset,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
    ) -> ReplayResult:
        """Replay ``dataset`` end to end through the execution path.

        The run records the dataset it consumed as its ``source_id``, and
        declares :attr:`~alphalab.runtime.run.ExecutionMode.REPLAY` -- which a
        replay could not say before v2.14, because it wrote a payload identical
        to a backtest's.
        """

        cursor_ids = id_source(
            None if config.seed is None else config.seed + REPLAY_CURSOR_SEED_OFFSET
        )

        with use_id_source(cursor_ids):
            replay = ReplayBacktest.initialize(dataset)

        with id_scope(config.seed):
            state = replace(
                initialize(replace(config, mode=ExecutionMode.REPLAY), strategy_state),
                source_id=dataset.dataset_id,
            )
            last: MarketRecord | None = None
            replayed = 0

            while True:
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
                state, _ = advance(state, record, context_factory)
                last = record
                replayed += 1

            return ReplayResult(
                backtest=finalize(state),
                replay_status=replay.status.name,
                records_replayed=replayed,
                last_record=last,
            )
