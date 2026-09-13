"""The session driver: a market-data source, a clock, and nothing else.

A trading session reads market records from a
:class:`~alphalab.market.source.MarketDataSource` and hands each one to
:meth:`~alphalab.runtime.run.RunEngine.advance` -- the canonical run step. That
is the whole loop, and it is the same loop :mod:`alphalab.backtesting` runs,
because as of v2.14 it is the same function on the same state.

**This module owns no state.** Until v2.14 it owned ``SessionState`` and
``SessionConfig``, and :mod:`alphalab.backtesting` owned a near-identical pair;
the two ``resume`` implementations were character-identical. ADR-0030 gives the
run one owner -- :class:`~alphalab.runtime.run.RunEngine` over
:class:`~alphalab.runtime.run.RunState` -- and leaves a driver with the two
things that genuinely differ between environments: where records come from, and
what clock judges them.

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
Run state                    RunState  RunState  RunState  RunState
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

Backtest, replay and paper run end to end through this module. **A live session
driven by this one still produces working orders and stops** -- it reads a
source and advances the run, and routing an accepted order is not something this
loop does.

**Use :class:`~alphalab.runtime.live.LiveSession` for a live run.** That is the
driver ADR-0030 anticipated and v2.16 wrote: it settles the fills a venue has
reported, advances the run through the same
:meth:`~alphalab.runtime.run.RunEngine.advance` this module calls, and routes
the orders that are newly working -- in that order, so a fill already known
reaches the portfolio before the strategy is dispatched. It also carries the
venue binding, which this module has nowhere to put.

This module is not deprecated and is not a lesser one: a paper session is a real
environment and this is its driver. What it is not is a live loop. Until v2.15
there was no live loop to point at, and until v2.16 there was none in this
repository; both sentences were here, and both are now out of date.

One thing remains true. The transport is written to protocol and **has not been
verified against any commercial venue** (see its docstring). See ADR-0031 and
ADR-0033.

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
from contextlib import AbstractContextManager
from dataclasses import replace

from alphalab.common.ids import id_scope
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.record import MarketRecord
from alphalab.market.source import MarketDataSource, OrderingGuarantee

# ``ExecutionMode`` is defined beside ``RunConfig``, the only thing that reads
# it, and re-exported here unchanged -- the pattern ``backtesting.dataset`` uses
# for ``MarketRecord`` and ``backtesting.engine`` for ``id_scope``. Defining it
# here instead would close an import cycle: this module imports ``RunEngine``.
from alphalab.runtime.execution_pipeline import ContextFactory, ExecutionPipelineResult
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine, RunState
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = ["ExecutionMode", "TradingSession"]


class TradingSession:
    """Drives a market-data source through the canonical run step.

    Stateless: every method delegates to
    :class:`~alphalab.runtime.run.RunEngine` and returns a
    :class:`~alphalab.runtime.run.RunState`. What this driver adds is the source
    and the clock.
    """

    @staticmethod
    def initialize(config: RunConfig, strategy_state: StrategyRuntimeState) -> RunState:
        """Fund the portfolio and build the state a session starts from."""

        return RunEngine.initialize(config, strategy_state)

    @staticmethod
    def resume(state: RunState) -> AbstractContextManager[None]:
        """The scope a restored session continues in.

        See :meth:`~alphalab.runtime.run.RunEngine.resume`, which is the only
        implementation of this contract in AlphaLab.
        """

        return RunEngine.resume(state)

    @staticmethod
    def advance(
        state: RunState,
        record: MarketRecord,
        context_factory: ContextFactory,
        now: float | None = None,
    ) -> tuple[RunState, ExecutionPipelineResult | None]:
        """Move one record through the execution path, unless it is too old.

        ``now`` is the session's clock. It defaults to the record's own
        timestamp, under which no record is ever stale -- the right answer for a
        historical run. A live session passes its real clock.

        See :meth:`~alphalab.runtime.run.RunEngine.advance` for the gates and
        their order.
        """

        return RunEngine.advance(state, record, context_factory, now)

    @staticmethod
    def run(
        config: RunConfig,
        source: MarketDataSource,
        strategy_state: StrategyRuntimeState,
        context_factory: ContextFactory,
        clock: Iterable[float] | None = None,
    ) -> RunState:
        """Read ``source`` to exhaustion through the execution path.

        ``clock`` supplies one reading per record for the staleness gate. Omit
        it and each record is judged against its own timestamp, which is what a
        historical source wants.

        Raises:
            MarketValidationError: If the source declares ``UNORDERED`` and the
                run's config requires ``CHRONOLOGICAL``. The mismatch is refused
                here, before any record is processed, rather than at whichever
                record happens to arrive out of order -- a session that would
                abort partway through a run should not start it. Set
                ``RunConfig.ordering`` to ``UNORDERED`` to accept the source and
                have regressing records skipped and recorded.
        """

        if (
            source.ordering is OrderingGuarantee.UNORDERED
            and config.ordering is OrderingGuarantee.CHRONOLOGICAL
        ):
            raise MarketValidationError(
                f"Source {source.source_id!r} declares UNORDERED records and this "
                "run requires CHRONOLOGICAL ones. AlphaLab does not reorder market "
                "data: the market engine takes the newest record it is given as "
                "current, so a record arriving late would mark the portfolio "
                "backwards. Set RunConfig.ordering to UNORDERED to skip and record "
                "such records instead."
            )

        readings = iter(clock) if clock is not None else None
        with id_scope(config.seed):
            # The source is in scope exactly here, and nowhere later: `advance`
            # takes one record at a time and never sees the stream it came from.
            state = replace(
                RunEngine.initialize(config, strategy_state), source_id=source.source_id
            )
            for record in source.records():
                now = next(readings, None) if readings is not None else None
                state, _ = RunEngine.advance(state, record, context_factory, now)
            return state
