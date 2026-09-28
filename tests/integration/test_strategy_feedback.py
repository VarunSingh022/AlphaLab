"""A strategy on the canonical path learns of its own fills and orders (ledger EXE-005).

Until v3.11 :class:`~alphalab.strategy.dispatcher.Dispatcher` routed
``FillEvent`` and ``OrderEvent`` to ``on_fill`` and ``on_order`` and nothing on
the execution path ever built one: a strategy traded blind to what its orders
did. The pipeline now delivers, after every step, each fill -- to every strategy
that asked for the order, with its share -- and what became of each order and
of each request that never became one, to the strategies subscribed to
``fills`` or ``orders``.

What a strategy asks for in answer rests until its asset's next event. Filling
it in the same step would let a strategy that answers every fill with an order
keep one step going for ever.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.common.ids import id_scope
from alphalab.execution.policy import FillTiming
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineResult,
    ExecutionPipelineState,
)
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import FillEvent, Intent, OrderEvent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    context_factory,
    permissive_risk_limits,
    pipeline_config,
    sized_quote,
)

ASSET = "0f8fad5b-d9cb-469f-a165-70867728950e"


class _Trader(BaseStrategy):
    """Trades a scripted delta at chosen instants and records the feedback it gets."""

    def __init__(
        self,
        strategy_id: str,
        plan: Mapping[float, Decimal],
        answer_fills_with: Decimal | None = None,
    ) -> None:
        self.strategy_id = strategy_id
        self.plan = dict(plan)
        self.answer = answer_fills_with
        self.fills: list[FillEvent] = []
        self.orders: list[OrderEvent] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        delta = self.plan.get(event.quote.timestamp)
        if delta is None:
            return ()
        return (Intent(self.strategy_id, ASSET, delta, timestamp=event.quote.timestamp),)

    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        self.fills.append(event)
        if self.answer is None:
            return ()
        return (Intent(self.strategy_id, ASSET, self.answer, timestamp=event.timestamp),)

    def on_order(self, context: StrategyContext, event: OrderEvent) -> Iterable[Intent]:
        self.orders.append(event)
        return ()


def _runtime(*entries: tuple[str, BaseStrategy, frozenset[str]]) -> RuntimeState:
    runtime = create_runtime()
    for strategy_id, strategy, _ in entries:
        runtime = register_strategy(runtime, strategy_id, strategy)
    running = {}
    for strategy_id, _, subscriptions in entries:
        entry = runtime.strategies[strategy_id]
        entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
        entry, _ = RuntimeSupervisor.subscribe(entry, subscriptions, 1.2)
        entry, _ = RuntimeSupervisor.start(entry, 1.3)
        running[strategy_id] = entry
    return replace(runtime, strategies=running)


def _pipeline(
    runtime: RuntimeState,
    timing: FillTiming = FillTiming.SAME_EVENT,
    max_order: Decimal = Decimal("100000"),
) -> ExecutionPipelineState:
    config = replace(
        pipeline_config("A", risk_limits=permissive_risk_limits(max_order)), fill_timing=timing
    )
    budget = replace(
        config.budget,
        strategy_budgets={strategy_id: Decimal("1000000") for strategy_id in runtime.strategies},
    )
    return ExecutionPipeline.initialize(replace(config, budget=budget), runtime, 1.0)


def _step(state: ExecutionPipelineState, at: float) -> ExecutionPipelineResult:
    return ExecutionPipeline.process_quote(
        state, sized_quote(ASSET, at, Decimal("100"), Decimal("1000")), context_factory
    )


FEEDBACK = frozenset({"quotes", "fills", "orders"})


def test_a_strategy_learns_of_its_fill_and_what_its_order_became() -> None:
    trader = _Trader("A", {2.0: Decimal("10")})
    result = _step(_pipeline(_runtime(("A", trader, FEEDBACK))), 2.0)

    (fill,) = trader.fills
    (report,) = result.execution_reports
    assert (fill.order_id, fill.execution_id) == (report.order_id, report.execution_id)
    assert (fill.fill_quantity, fill.fill_price) == (report.fill_quantity, report.fill_price)
    assert (fill.side, fill.attributed_quantity) == ("buy", Decimal("10"))
    (order,) = trader.orders
    assert (order.order_id, order.status) == (report.order_id, "filled")
    assert (order.quantity, order.filled_quantity) == (Decimal("10"), Decimal("10"))


def test_a_netted_fill_is_divided_by_contribution_and_sums_to_the_fill() -> None:
    buyer = _Trader("A", {2.0: Decimal("60")})
    seller = _Trader("B", {2.0: Decimal("-20")})
    result = _step(_pipeline(_runtime(("A", buyer, FEEDBACK), ("B", seller, FEEDBACK))), 2.0)

    (report,) = result.execution_reports
    assert report.fill_quantity == Decimal("40")  # one netted BUY 40
    (a,) = buyer.fills
    (b,) = seller.fills
    assert (a.attributed_quantity, b.attributed_quantity) == (Decimal("60"), Decimal("-20"))
    assert a.attributed_quantity is not None and b.attributed_quantity is not None
    assert a.attributed_quantity + b.attributed_quantity == report.fill_quantity
    assert [event.status for event in buyer.orders] == ["filled"]
    assert [event.status for event in seller.orders] == ["filled"]


def test_a_request_risk_refused_is_reported_as_rejected_with_its_reason() -> None:
    trader = _Trader("A", {2.0: Decimal("10")})
    result = _step(_pipeline(_runtime(("A", trader, FEEDBACK)), max_order=Decimal("5")), 2.0)

    assert result.oms_orders == ()
    assert trader.fills == []
    (rejected,) = trader.orders
    assert rejected.status == "rejected"
    assert rejected.reason == result.risk_decisions[0].reason
    assert rejected.quantity is None


def test_an_order_working_across_events_reports_each_step_it_ends() -> None:
    trader = _Trader("A", {2.0: Decimal("10")})
    state = _pipeline(_runtime(("A", trader, FEEDBACK)), FillTiming.NEXT_EVENT)

    state = _step(state, 2.0).state
    assert [event.status for event in trader.orders] == ["accepted"]
    assert trader.fills == []
    _step(state, 3.0)
    assert [event.status for event in trader.orders] == ["accepted", "filled"]
    assert [fill.timestamp for fill in trader.fills] == [3.0]


def test_an_answer_to_a_fill_rests_until_the_next_event() -> None:
    """A strategy answering every fill with an order cannot keep a step going."""

    trader = _Trader("A", {2.0: Decimal("10")}, answer_fills_with=Decimal("1"))
    state = _pipeline(_runtime(("A", trader, FEEDBACK)))

    first = _step(state, 2.0)
    # The answer to the first fill is an order, working -- not filled here.
    assert len(first.execution_reports) == 1
    working = first.state.oms.working_orders_for(ASSET)
    assert [first.state.oms.orders.find(order_id).quantity for order_id in working] == [
        Decimal("1")
    ]
    assert len(first.intents) == 2

    second = _step(first.state, 3.0)
    # It fills at the asset's next event, and its own fill is answered in turn.
    assert [report.fill_quantity for report in second.execution_reports] == [Decimal("1")]
    assert second.state.portfolio.positions[ASSET].quantity == Decimal("11")
    assert len(second.state.oms.working_orders_for(ASSET)) == 1


def test_a_strategy_that_did_not_subscribe_gets_no_feedback_and_nothing_moves() -> None:
    """The run of a quotes-only strategy is the run it was before v3.11."""

    def run(subscriptions: frozenset[str]) -> tuple[_Trader, ExecutionPipelineResult]:
        trader = _Trader("A", {2.0: Decimal("10"), 3.0: Decimal("-4")})
        with id_scope(77):
            state = _pipeline(_runtime(("A", trader, subscriptions)))
            state = _step(state, 2.0).state
            return trader, _step(state, 3.0)

    quiet, quiet_result = run(frozenset({"quotes"}))
    told, told_result = run(FEEDBACK)

    assert quiet.fills == [] and quiet.orders == []
    assert len(told.fills) == 2 and len(told.orders) == 2
    # Feedback draws nothing from the run's identifier stream: every order,
    # fill and report is the same run either way.
    assert [str(o.order_id.value) for o in quiet_result.oms_orders] == [
        str(o.order_id.value) for o in told_result.oms_orders
    ]
    assert quiet_result.execution_reports == told_result.execution_reports
    assert quiet_result.state.portfolio.positions == told_result.state.portfolio.positions


# --------------------------------------------------------------------------- #
# on_shutdown and on_stop
# --------------------------------------------------------------------------- #


class _Flattener(_Trader):
    """Buys, then flattens as it is stopped, and records the order of the hooks."""

    def __init__(self, strategy_id: str) -> None:
        super().__init__(strategy_id, {2.0: Decimal("10")})
        self.hooks: list[str] = []

    def on_shutdown(self, context: StrategyContext) -> Iterable[Intent]:
        self.hooks.append("shutdown")
        return (Intent(self.strategy_id, ASSET, Decimal("-10"), timestamp=5.0),)

    def on_stop(self, context: StrategyContext) -> None:
        self.hooks.append("stop")


def test_stopping_delivers_shutdown_then_stop_and_rests_the_flattening_order() -> None:
    from alphalab.strategy.state import LifecycleState

    flattener = _Flattener("A")
    state = _step(_pipeline(_runtime(("A", flattener, FEEDBACK))), 2.0).state
    assert state.portfolio.positions[ASSET].quantity == Decimal("10")

    seen_before_stop = (len(flattener.fills), len(flattener.orders))
    stopped, intents, orders = ExecutionPipeline.stop_strategies(state, context_factory, 5.0)

    assert flattener.hooks == ["shutdown", "stop"]
    assert stopped.strategy.strategies["A"].status is LifecycleState.STOPPED
    assert [intent.target for intent in intents] == [Decimal("-10")]
    (order,) = orders
    assert (order.quantity, order.side.value, order.created_at) == (Decimal("10"), "sell", 5.0)
    # Resting: nothing trades at a price the run has not observed ...
    assert stopped.portfolio.positions[ASSET].quantity == Decimal("10")
    # ... and the asset's next event fills it. A stopped strategy is sent nothing.
    after = _step(stopped, 6.0)
    assert [report.fill_quantity for report in after.execution_reports] == [Decimal("10")]
    assert ASSET not in after.state.portfolio.positions
    assert (len(flattener.fills), len(flattener.orders)) == seen_before_stop, (
        "a stopped strategy is delivered no feedback"
    )


def test_a_shutdown_hook_that_raises_fails_the_strategy_and_places_nothing() -> None:
    from alphalab.strategy.state import LifecycleState

    class Broken(_Flattener):
        def on_shutdown(self, context: StrategyContext) -> Iterable[Intent]:
            raise RuntimeError("cannot flatten")

    state = _step(_pipeline(_runtime(("A", Broken("A"), FEEDBACK))), 2.0).state
    stopped, intents, orders = ExecutionPipeline.stop_strategies(state, context_factory, 5.0)

    failed = stopped.strategy.strategies["A"]
    assert failed.status is LifecycleState.FAILED
    assert "cannot flatten" in (failed.last_error or "")
    assert intents == () and orders == ()


def test_strategies_cannot_be_stopped_before_the_last_event() -> None:
    import pytest

    from alphalab.runtime.exceptions import RuntimeValidationError

    state = _step(_pipeline(_runtime(("A", _Flattener("A"), FEEDBACK))), 2.0).state
    with pytest.raises(RuntimeValidationError, match="before the last event"):
        ExecutionPipeline.stop_strategies(state, context_factory, 1.5)


def test_a_run_stops_its_strategies_only_when_told_to() -> None:
    from alphalab.persistence import deserialize, serialize
    from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
    from alphalab.runtime.snapshot import capture, from_primitives
    from alphalab.strategy.state import LifecycleState

    flattener = _Flattener("A")
    config = RunConfig(
        pipeline=_pipeline(_runtime(("A", flattener, FEEDBACK))).config,
        mode=ExecutionMode.BACKTEST,
        start_timestamp=1.0,
        compile_analytics=False,
    )
    run = RunEngine.initialize(config, _runtime(("A", flattener, FEEDBACK)))
    from alphalab.market.record import MarketRecord

    record = MarketRecord("DS", 2.0, sized_quote(ASSET, 2.0, Decimal("100"), Decimal("1000")))
    run, _ = RunEngine.advance(run, record, context_factory)
    finalized = RunEngine.finalize(run)
    assert finalized.pipeline.strategy.strategies["A"].status is LifecycleState.RUNNING
    assert flattener.hooks == []

    stopped = RunEngine.stop(run, context_factory)
    assert flattener.hooks == ["shutdown", "stop"]
    assert [order.quantity for order in stopped.working_orders] == [Decimal("10")]
    payload = deserialize(serialize(capture(stopped.pipeline)))
    assert payload["strategy"][0]["status"] == "LifecycleState.STOPPED"
    assert from_primitives(payload).strategy[0].status is LifecycleState.STOPPED


# --------------------------------------------------------------------------- #
# Timers
# --------------------------------------------------------------------------- #


class _OnTimer(_Trader):
    def __init__(self, strategy_id: str) -> None:
        super().__init__(strategy_id, {})
        self.timers: list[str] = []

    def on_timer(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.timers.append(event.timer_id)
        return (Intent(self.strategy_id, ASSET, Decimal("5"), timestamp=event.timestamp),)


def test_a_timer_reaches_its_subscribers_and_its_orders_rest() -> None:
    from alphalab.strategy.events import TimerEvent

    subscribed, deaf = _OnTimer("A"), _OnTimer("B")
    state = _pipeline(
        _runtime(
            ("A", subscribed, frozenset({"quotes", "timers", "orders"})), ("B", deaf, FEEDBACK)
        )
    )
    state = _step(state, 2.0).state

    state, intents, orders = ExecutionPipeline.process_timer(
        state, TimerEvent("T-1", 2.5, "rebalance", "daily"), context_factory
    )

    assert subscribed.timers == ["rebalance"] and deaf.timers == []
    assert [intent.strategy_id for intent in intents] == ["A"]
    (order,) = orders
    assert (order.quantity, order.created_at) == (Decimal("5"), 2.5)
    assert [event.status for event in subscribed.orders] == ["accepted"]
    assert ASSET not in state.portfolio.positions, "a timer observes no price to fill at"
    filled = _step(state, 3.0)
    assert [report.fill_quantity for report in filled.execution_reports] == [Decimal("5")]
