"""A strategy hears the fills of the children its orders are worked in (ADR-0061, GAP-45).

Every figure here is calculated by hand from the queue suite's records
(tests/integration/test_queue_model.py) and the plans-with-queues suite's
one-slice TWAP, which works a buy of 100 at 99.99 in one limit child.

* **What is heard.** Under the queue model the child fills 20, 50 and 30 at the
  second, third and fourth records; a strategy defining ``on_child_fill`` and
  subscribed to ``child_fills`` hears each, its share the whole fill, a buy.
  Without the model the child fills all 100 at the first print, heard once.
* **An answer is an intent like any other.** Answering the fill of 20 with a
  sale of 10 places an order at that instant, resting: the plan adopts it at
  the asset's next record and releases one market child, which fills at the
  record after, the last quote's midpoint of 99.985 -- and that fill is heard
  too, its share signed, a sale of 10.
* **Who is not told.** A strategy without the hook, or not subscribed: it hears
  nothing, and its fills are the plain run's.
* **The drivers agree.** A replay hears what the backtest hears, and a replay
  paused at any boundary resumes to the same result.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.backtesting import (
    AlgorithmAssignment,
    BacktestEngine,
    ExecutionPlan,
    PausedReplay,
    ReplayBacktest,
    ReplayResult,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import MARKET
from alphalab.core.enums import OrderStatus
from alphalab.execution.algorithms import TWAP, Urgency
from alphalab.execution.policy import ImmediateFill
from alphalab.execution.queue import QueueModel
from alphalab.lifecycle import digest_run
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import BarClosed
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.execution_pipeline import ExecutionPipeline
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy import (
    ChildFillReceived,
    ChildFillStrategyProtocol,
    defines_on_child_fill,
)
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration import test_queue_model as queues
from tests.integration.harness import (
    context_factory,
    equity,
    pipeline_config,
    registry_of,
    running_strategy_state,
)

D = Decimal
T0 = queues.T0
LIMIT = queues.LIMIT
WHOLE = ExecutionPlan(AlgorithmAssignment(TWAP(1, Urgency.neutral()), 600.0, D("1")))
EVERYTHING = frozenset({"*"})


class Hears(queues.Poster):
    """The queue suite's buyer, which also hears its children's fills.

    ``answer`` sells 10 on hearing a fill of 20: a decision taken from the event
    alone, so a replay resumed with a fresh instance decides as one never paused.
    """

    def __init__(self, answer: bool = False) -> None:
        super().__init__()
        self.heard: list[ChildFillReceived] = []
        self._answer = answer

    def on_child_fill(self, context: StrategyContext, event: ChildFillReceived) -> Iterable[Intent]:
        self.heard.append(event)
        if self._answer and event.fill_quantity == D("20"):
            return (Intent("S", queues.ACME, D("-10"), terms=MARKET, timestamp=event.timestamp),)
        return ()


def _strategies(strategy: Any, subscriptions: frozenset[str] = EVERYTHING) -> StrategyRuntimeState:
    state = register_strategy(create_runtime(), "S", strategy)
    running = {}
    for strategy_id, entry in state.strategies.items():
        configured, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
        subscribed, _ = RuntimeSupervisor.subscribe(initialized, subscriptions, 1.2)
        running[strategy_id], _ = RuntimeSupervisor.start(subscribed, 1.3)
    return replace(state, strategies=running)


def _run(
    strategy: Any,
    model: QueueModel | None = queues.QUEUE,
    subscriptions: frozenset[str] = EVERYTHING,
) -> Any:
    return BacktestEngine.run(
        queues.config(),
        queues.dataset(queues.DEPLETION),
        _strategies(strategy, subscriptions),
        context_factory,
        execution=WHOLE,
        queue=model,
    )


def _heard(strategy: Hears) -> list[tuple[float, Decimal, Decimal | None, Decimal, str]]:
    return [
        (e.timestamp - T0, e.fill_quantity, e.attributed_quantity, e.fill_price, e.side)
        for e in strategy.heard
    ]


# --------------------------------------------------------------------------- #
# What is heard
# --------------------------------------------------------------------------- #


def test_a_strategy_hears_each_childs_fill_with_its_share() -> None:
    strategy = Hears()
    result = _run(strategy)
    assert _heard(strategy) == [
        (2.0, D("20"), D("20"), LIMIT, "buy"),
        (3.0, D("50"), D("50"), LIMIT, "buy"),
        (4.0, D("30"), D("30"), LIMIT, "buy"),
    ]
    parent = queues.only_order(result)
    parent_id = str(parent.order_id.value)
    assert {e.parent_order_id for e in strategy.heard} == {parent_id}
    assert {e.child_id for e in strategy.heard} == {f"{parent_id}/1"}
    assert {e.instrument for e in strategy.heard} == {queues.ACME}
    # Each fill once, by the execution report it settled through on the parent.
    reports = [r.execution_id for r in result.state.execution.history]
    assert [e.execution_id for e in strategy.heard] == reports
    assert [f.execution_id for f in result.algorithms.fills] == reports


class HearsAs(Hears):
    """``Hears`` as ``strategy_id``, buying ``quantity`` at the limit at the first instant."""

    def __init__(self, strategy_id: str, quantity: str) -> None:
        super().__init__()
        self._id = strategy_id
        self._quantity = D(quantity)

    def _place(self, timestamp: float) -> Iterable[Intent]:
        if timestamp != T0:
            return ()
        return (Intent(self._id, queues.ACME, self._quantity, terms=self._terms, timestamp=T0),)


def test_each_contributor_to_a_netted_parent_hears_its_own_share() -> None:
    """Sixty from S and forty from T net into one parent of 100, worked in one child.

    Each of its fills -- 20, 50 and 30 -- is divided by contribution: S hears
    12, 30 and 18, and T the rest, 8, 20 and 12.
    """

    s, t = HearsAs("S", "60"), HearsAs("T", "40")
    state = create_runtime()
    for strategy_id, strategy in (("S", s), ("T", t)):
        state = register_strategy(state, strategy_id, strategy)
    running = {}
    for strategy_id, entry in state.strategies.items():
        configured, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
        subscribed, _ = RuntimeSupervisor.subscribe(initialized, EVERYTHING, 1.2)
        running[strategy_id], _ = RuntimeSupervisor.start(subscribed, 1.3)
    config = queues.config()
    budget = config.pipeline.budget
    half = budget.global_capital / 2
    shared = replace(budget, strategy_budgets={"S": half, "T": half})
    result = BacktestEngine.run(
        replace(config, pipeline=replace(config.pipeline, budget=shared)),
        queues.dataset(queues.DEPLETION),
        replace(state, strategies=running),
        context_factory,
        execution=WHOLE,
        queue=queues.QUEUE,
    )
    parent = queues.only_order(result)
    assert parent.quantity == D("100") and parent.status is OrderStatus.FILLED
    assert _heard(s) == [
        (2.0, D("20"), D("12"), LIMIT, "buy"),
        (3.0, D("50"), D("30"), LIMIT, "buy"),
        (4.0, D("30"), D("18"), LIMIT, "buy"),
    ]
    assert _heard(t) == [
        (2.0, D("20"), D("8"), LIMIT, "buy"),
        (3.0, D("50"), D("20"), LIMIT, "buy"),
        (4.0, D("30"), D("12"), LIMIT, "buy"),
    ]
    assert [e.execution_id for e in s.heard] == [e.execution_id for e in t.heard]


WIDGET = equity("WIDGET")


class AnswersAChildFill(BaseStrategy):
    """Buys 10 at the bar stamped 1.0, and answers any child's fill with a sale of 5."""

    def __init__(self) -> None:
        self.heard: list[ChildFillReceived] = []

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.bar.timestamp != 1.0:
            return ()
        return (Intent("W", WIDGET.asset_id, D("10"), terms=MARKET, timestamp=1.0),)

    def on_child_fill(self, context: StrategyContext, event: ChildFillReceived) -> Iterable[Intent]:
        self.heard.append(event)
        return (Intent("W", WIDGET.asset_id, D("-5"), terms=MARKET, timestamp=event.timestamp),)


def _closed(at: float, price: str) -> BarClosed:
    p = D(price)
    bar = Bar(WIDGET.asset_id, at, p, p, p, p, D("1000000"), None, None, TimeFrame.D1)
    return BarClosed(event_id=f"B-{at}", timestamp=at, bar=bar)


def test_an_answer_rests_until_its_assets_next_event_whoever_fills_the_run() -> None:
    """What a strategy answers to a child's fill rests until its asset's next event.

    As an answer to a fill does (EXE-005): it is never filled in the step that
    reported the fill. Under a plan the plan adopts it there; on a run that fills
    its own orders, reporting a fill of the strategy's purchase as a child's, the
    sale of 5 is accepted at 1.0 and fills at the next bar, 101 at 2.0.
    """

    strategy = AnswersAChildFill()
    config = replace(pipeline_config("W"), instruments=registry_of(WIDGET))
    state = ExecutionPipeline.initialize(config, running_strategy_state("W", strategy), 0.5)
    bought = ExecutionPipeline.process_market_event(state, _closed(1.0, "100"), context_factory)
    (report,) = bought.state.execution.history
    delivered, answered = ExecutionPipeline.deliver_child_fills(
        bought.state,
        _closed(1.0, "100"),
        ((f"{report.order_id}/1", report),),
        context_factory,
        ImmediateFill(),
    )
    assert [(e.fill_quantity, e.attributed_quantity) for e in strategy.heard] == [
        (D("10"), D("10"))
    ]
    assert [intent.target for intent in answered] == [D("-5")]
    assert len(delivered.execution.history) == 1  # nothing filled in the reporting step
    sale = delivered.oms.orders.orders()[-1]
    assert (sale.side.value, sale.quantity, sale.status) == ("sell", D("5"), OrderStatus.ACCEPTED)
    following = ExecutionPipeline.process_market_event(
        delivered, _closed(2.0, "101"), context_factory
    )
    assert [(f.price, f.quantity, f.filled_at) for f in following.fills] == [
        (D("101"), D("5"), 2.0)
    ]


def test_without_a_queue_the_one_fill_is_heard_once() -> None:
    strategy = Hears()
    _run(strategy, model=None)
    assert _heard(strategy) == [(1.0, D("100"), D("100"), LIMIT, "buy")]


def test_what_a_strategy_answers_is_an_intent_like_any_other() -> None:
    strategy = Hears(answer=True)
    result = _run(strategy)
    buy, sell = result.state.oms.orders.orders()
    # Placed at the fill it answered, resting: adopted at the next record (3.0),
    # its one market child released there and filled at the last quote (4.0).
    assert (sell.side.value, sell.quantity, sell.created_at - T0) == ("sell", D("10"), 2.0)
    assert sell.status is OrderStatus.FILLED and buy.status is OrderStatus.FILLED
    assert str(sell.order_id.value) in result.algorithms.finished
    assert queues.fills(result)[-1] == (4.0, D("10"), D("99.985"), "TAKER")
    # The sale's child fill is heard too, its share signed.
    assert _heard(strategy)[-1] == (4.0, D("10"), D("-10"), D("99.985"), "sell")
    assert strategy.heard[-1].parent_order_id == str(sell.order_id.value)
    assert [q for _, q, *_ in _heard(strategy)] == [D("20"), D("50"), D("30"), D("10")]


# --------------------------------------------------------------------------- #
# Who is not told
# --------------------------------------------------------------------------- #


def test_only_a_subscribed_strategy_defining_the_hook_hears() -> None:
    assert defines_on_child_fill(Hears())
    assert isinstance(Hears(), ChildFillStrategyProtocol)
    assert not defines_on_child_fill(queues.Poster())

    plain = _run(queues.Poster())
    unsubscribed = Hears()
    result = _run(unsubscribed, subscriptions=frozenset({"quotes", "trades", "fills", "orders"}))
    assert unsubscribed.heard == []
    assert queues.fills(result) == queues.fills(plain)
    assert result.valuation == plain.valuation


# --------------------------------------------------------------------------- #
# The drivers agree
# --------------------------------------------------------------------------- #


def _replay(strategy: Any, pause: int | None = None) -> ReplayResult | PausedReplay:
    return ReplayBacktest.run(
        queues.config(),
        queues.dataset(queues.DEPLETION),
        _strategies(strategy),
        context_factory,
        execution=WHOLE,
        queue=queues.QUEUE,
        pause_when=lambda progress: pause is not None and progress.records_replayed == pause,
    )


def test_a_replay_hears_what_the_backtest_hears() -> None:
    backtest, replay = Hears(answer=True), Hears(answer=True)
    expected = _run(backtest)
    replayed = _replay(replay)
    assert isinstance(replayed, ReplayResult)
    assert _heard(replay) == _heard(backtest)
    assert queues.fills(replayed.backtest) == queues.fills(expected)
    assert replayed.backtest.valuation == expected.valuation


def test_a_replay_paused_anywhere_resumes_to_the_same_result() -> None:
    whole = _replay(Hears(answer=True))
    assert isinstance(whole, ReplayResult)
    for pause in range(1, len(queues.DEPLETION)):
        paused = _replay(Hears(answer=True), pause)
        assert isinstance(paused, PausedReplay)
        pipeline = paused.run.config.pipeline
        objects = RunObjects(
            RuntimeObjects(
                pipeline.sizing_model,
                pipeline.simulator,
                {"S": Hears(answer=True)},
                pipeline.instruments,
            ),
            paused.run.config.fill_policy,
        )
        payload = dict(deserialize(serialize(capture_replay(paused))))
        restored = restore_replay(
            replay_from_primitives(payload),
            objects,
            queues.dataset(queues.DEPLETION),
            execution=WHOLE,
            queue=queues.QUEUE,
        )
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert isinstance(resumed, ReplayResult)
        assert digest_run(resumed.backtest).result_id == digest_run(whole.backtest).result_id
        assert queues.fills(resumed.backtest) == queues.fills(whole.backtest)
