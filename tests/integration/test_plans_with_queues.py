"""An execution plan's resting children wait their turn in the queue (ADR-0061, GAP-44).

Every figure here is calculated by hand from the queue suite's records
(tests/integration/test_queue_model.py): a quote showing 300 bid at 99.99, then
sellers hitting the bid for 120, 200 and 50, then the offer coming down to the
limit.

* **One child, the whole parent.** A one-slice TWAP works a buy of 100 at 99.99
  in one limit child. Without a queue model it fills all 100 at the first print
  at its limit. Under one it joins behind the 300 shown, and fills 20 of the
  200 that traded beyond the queue, then the 50 at the front, then the last 30
  when the offer touches its limit -- what the same order fills resting on its
  own (the queue suite's ``test_a_resting_buy...``).
* **Slices.** A two-slice TWAP over four seconds: the first child expires at
  the end of its slice still 180 behind, and leaves the book; the second, placed
  at a print, which shows no queue, starts from an unknown one, and nothing
  fills it before the window closes.
* **What is not governed.** Market children take the market and never rest: a
  queue changes nothing for them, and the run's record is the plan's alone.
* **One result, both books**, which a replay holds too, and a replay paused at
  any boundary resumes to the same queue and result.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    AlgorithmAssignment,
    AlgorithmicQueuedResult,
    AlgorithmicResult,
    BacktestEngine,
    ExecutionPlan,
    PausedReplay,
    QueuedResult,
    QueueModelError,
    ReplayBacktest,
    ReplayResult,
)
from alphalab.backtesting.algorithmic import AlgorithmBook, algorithmic_run
from alphalab.backtesting.queueing import queued_run
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import MARKET
from alphalab.core.enums import OrderStatus
from alphalab.execution.algorithms import TWAP, Urgency
from alphalab.execution.queue import QueuePosition
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.run import ExecutionMode
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_queue_model as queues
from tests.integration.harness import context_factory

D = Decimal
LIMIT = queues.LIMIT
#: One limit child of the whole parent, worked for ten minutes.
WHOLE = ExecutionPlan(AlgorithmAssignment(TWAP(1, Urgency.neutral()), 600.0, D("1")))
#: Two children of 50, each with two seconds to fill.
SLICES = ExecutionPlan(AlgorithmAssignment(TWAP(2, Urgency.neutral()), 4.0, D("1")))


def _run(
    plan: ExecutionPlan,
    queued: bool = True,
    records: Any = queues.DEPLETION,
    poster: queues.Poster | None = None,
) -> Any:
    return BacktestEngine.run(
        queues.config(),
        queues.dataset(records),
        queues.strategies(poster),
        context_factory,
        execution=plan,
        queue=queues.QUEUE if queued else None,
    )


def _children(result: Any) -> list[tuple[float, Decimal, OrderStatus | None]]:
    """Each child's release instant, quantity and outcome, in release order."""

    book: AlgorithmBook = result.algorithms
    finished = (f.algorithm for f in book.finished.values() if f.algorithm is not None)
    algorithms = [*book.working.values(), *finished]
    assert len(algorithms) == 1  # the one parent
    algorithm = algorithms[0]
    return [
        (p.child.released_at - queues.T0, p.child.quantity, p.outcome)
        for p in algorithm.children.values()
    ]


# --------------------------------------------------------------------------- #
# What the queue does to a plan's limit children
# --------------------------------------------------------------------------- #


def test_a_limit_child_fills_only_what_trades_beyond_the_queue_ahead_of_it() -> None:
    plain = _run(WHOLE, queued=False)
    assert type(plain) is AlgorithmicResult
    assert queues.fills(plain) == [(1.0, D("100"), LIMIT, "MAKER")]

    queued = _run(WHOLE)
    assert isinstance(queued, AlgorithmicQueuedResult)
    assert queues.fills(queued) == [
        (2.0, D("20"), LIMIT, "MAKER"),  # 200 printed, 180 of it the queue's
        (3.0, D("50"), LIMIT, "MAKER"),  # at the front: the whole print
        (4.0, D("30"), LIMIT, "MAKER"),  # the offer touches the limit
    ]
    # What the same order fills resting on its own under the model.
    assert queues.fills(queued) == queues.fills(queues.run())
    assert _children(queued) == [(0.0, D("100"), OrderStatus.FILLED)]
    parent = queues.only_order(queued)
    assert parent.status is OrderStatus.FILLED and parent.filled_quantity == D("100")
    assert sum(f.quantity for f in queued.algorithms.fills) == D("100")
    assert not queued.queue.positions  # nothing left resting


def test_a_child_still_short_of_the_front_keeps_its_place() -> None:
    queued = _run(WHOLE, records=queues.SHORT_OF_THE_FRONT)
    assert queues.fills(queued) == []
    ((released, quantity, outcome),) = _children(queued)
    assert (released, quantity, outcome) == (0.0, D("100"), None)
    (child_id,) = queued.queue.positions
    assert child_id.startswith(str(queues.only_order(queued).order_id.value))
    assert queued.queue.positions[child_id] == QueuePosition(LIMIT, D("50"))  # 300 - 120 - 130


def test_an_expired_slice_leaves_the_book_and_one_placed_at_a_print_starts_unknown() -> None:
    plain = _run(SLICES, queued=False)
    assert queues.fills(plain) == [
        (1.0, D("50"), LIMIT, "MAKER"),
        (3.0, D("50"), LIMIT, "MAKER"),
    ]
    queued = _run(SLICES, records=queues.SHORT_OF_THE_FRONT)
    # The first child, 180 behind when its slice ends at 2.0, expires there; the
    # second, released at that print with what the first left, joins an unknown
    # queue, and the window closes before anything fills it.
    assert _children(queued) == [
        (0.0, D("50"), OrderStatus.EXPIRED),
        (2.0, D("100"), None),
    ]
    assert queues.fills(queued) == []
    assert list(queued.queue.positions.values()) == [QueuePosition(LIMIT, None)]


def test_market_children_are_not_governed_and_the_run_is_recorded_as_the_plans() -> None:
    plain = _run(WHOLE, queued=False, poster=queues.Poster(MARKET))
    queued = _run(WHOLE, poster=queues.Poster(MARKET))
    assert queues.fills(queued) == queues.fills(plain) == [(1.0, D("100"), LIMIT, "TAKER")]
    # The queue book is driver state: the run's record and identities hold.
    assert digest_run(queued) == digest_run(plain)
    assert not queued.queue.positions


def test_the_result_holds_both_books_and_names_the_model() -> None:
    queued = _run(WHOLE)
    assert isinstance(queued, AlgorithmicQueuedResult)
    # An AlgorithmicResult that holds a queue, not a QueuedResult that holds a plan.
    assert isinstance(queued, AlgorithmicResult) and not isinstance(queued, QueuedResult)
    assert queued.algorithms.plan == WHOLE
    assert queued.queue.model == queues.QUEUE
    assert queued.execution_assumptions.queue_model == "PriceTimeQueue(count_unsigned_trades=False)"
    assert queued.execution_assumptions.routing is ExecutionRouting.ALGORITHMIC


def test_a_queue_model_is_refused_only_where_a_venue_fills_the_orders() -> None:
    config = queues.config()
    live = replace(config, mode=ExecutionMode.LIVE)
    assert live.pipeline.routing is ExecutionRouting.EXTERNAL
    with pytest.raises(QueueModelError, match="routes EXTERNAL"):
        queued_run(live, queues.QUEUE)
    # Under a plan the queue is the children's: what was refused before v4.3.
    planned, _ = algorithmic_run(config, WHOLE, ExecutionMode.BACKTEST)
    assert planned.pipeline.routing is ExecutionRouting.ALGORITHMIC
    assert queued_run(planned, queues.QUEUE) is not None


# --------------------------------------------------------------------------- #
# The replay, paused and resumed
# --------------------------------------------------------------------------- #


def _replay(pause: int | None = None) -> ReplayResult | PausedReplay:
    return ReplayBacktest.run(
        queues.config(),
        queues.dataset(queues.DEPLETION),
        queues.strategies(),
        context_factory,
        execution=WHOLE,
        queue=queues.QUEUE,
        pause_when=lambda progress: pause is not None and progress.records_replayed == pause,
    )


def _objects(paused: PausedReplay) -> RunObjects:
    pipeline = paused.run.config.pipeline
    return RunObjects(
        RuntimeObjects(
            pipeline.sizing_model, pipeline.simulator, {"S": queues.Poster()}, pipeline.instruments
        ),
        paused.run.config.fill_policy,
    )


def test_the_replay_equals_the_backtest_with_both_books() -> None:
    backtest = _run(WHOLE)
    replayed = _replay()
    assert isinstance(replayed, ReplayResult)
    assert isinstance(replayed.backtest, AlgorithmicQueuedResult)
    assert replayed.queue == backtest.queue
    assert replayed.algorithms is not None
    assert queues.fills(replayed.backtest) == queues.fills(backtest)
    assert replayed.backtest.valuation == backtest.valuation


def test_a_replay_paused_anywhere_resumes_to_the_same_books_and_result() -> None:
    whole = _replay()
    assert isinstance(whole, ReplayResult)
    for pause in range(1, len(queues.DEPLETION)):
        paused = _replay(pause)
        assert isinstance(paused, PausedReplay)
        assert paused.algorithms is not None and paused.queue is not None
        # Every child still working keeps its place; nothing else is kept.
        working = {
            c.child_id for a in paused.algorithms.working.values() for c in a.working_children
        }
        assert set(paused.queue.positions) == working
        payload = dict(deserialize(serialize(capture_replay(paused))))
        restored = restore_replay(
            replay_from_primitives(payload),
            _objects(paused),
            queues.dataset(queues.DEPLETION),
            execution=WHOLE,
            queue=queues.QUEUE,
        )
        assert restored.queue == paused.queue
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert isinstance(resumed, ReplayResult)
        assert digest_run(resumed.backtest).result_id == digest_run(whole.backtest).result_id
        assert resumed.queue == whole.queue
        assert queues.fills(resumed.backtest) == queues.fills(whole.backtest)
