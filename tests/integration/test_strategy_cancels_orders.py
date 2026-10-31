"""A strategy withdraws its own working orders (v4.2.0, the global gap audit).

Until v4.2.0 a strategy in a historical run could place a resting limit or stop
and never take it back: an order left only by filling or by its time in force.
A strategy re-targeting while one rested traded against its own unfilled order
-- a target of zero with a resting buy for 100 sold 100 short, and the stale buy
filled later. ``Intent(kind=IntentKind.CANCEL, instrument=...)`` withdraws every
order working in the instrument that the strategy alone contributed to, at the
instant of the event whose hook asked, after that event's fills and before the
batch's orders are placed: a cancel and a new limit in one return replace a
quote. An order netted with another strategy's, or worked by an execution plan,
is left working and recorded as a ``CancelRefused``.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import MARKET, OrderTerms
from alphalab.core.enums import OrderStatus
from alphalab.execution.queue import PriceTimeQueue
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.types import TransactionType
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import CancelRefused, Intent, IntentKind
from alphalab.strategy.exceptions import InvalidIntentError
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.supervisor import RuntimeSupervisor
from alphalab.strategy.validation import validate_intent
from tests.integration import test_algorithms_on_the_historical_path as algorithms
from tests.integration.harness import backtest_config, context_factory, equity, quote

D = Decimal
ACME = equity("ACME").asset_id
#: The market at 100, then dipping to 89 at 4.0: a bid at 90 fills there.
MIDS = {2.0: "100", 3.0: "100", 4.0: "89", 5.0: "100"}
BID = OrderTerms.limit(D("90"))


def target(quantity: str, at: float, terms: OrderTerms = MARKET, sid: str = "S") -> Intent:
    return Intent(
        sid, ACME, D(quantity), timestamp=at, kind=IntentKind.TARGET_QUANTITY, terms=terms
    )


def cancel(at: float, sid: str = "S") -> Intent:
    return Intent(sid, ACME, D("0"), timestamp=at, kind=IntentKind.CANCEL)


class Scripted(BaseStrategy):
    """Returns scripted intents at chosen quotes, and keeps every order status it hears."""

    def __init__(self, script: dict[float, tuple[Intent, ...]]) -> None:
        self._script = script
        self.heard: list[tuple[float, str]] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._script.get(event.quote.timestamp, ())

    def on_order(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.heard.append((event.timestamp, str(event.status)))
        return ()


def runtime(**strategies: BaseStrategy) -> Any:
    state = create_runtime()
    for sid, strategy in strategies.items():
        state = register_strategy(state, sid, strategy)
    running = {}
    for sid, entry in state.strategies.items():
        entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
        entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
        running[sid], _ = RuntimeSupervisor.start(entry, 1.3)
    from dataclasses import replace

    return replace(state, strategies=running)


def data() -> MarketDataset:
    return MarketDataset.of("CANCEL", [quote(ACME, t, D(m)) for t, m in MIDS.items()])


def config(*sids: str) -> Any:
    from dataclasses import replace

    base = backtest_config(sids[0] if sids else "S")
    budget = replace(
        base.pipeline.budget, strategy_budgets=dict.fromkeys(sids or ("S",), D("100000"))
    )
    return replace(base, pipeline=replace(base.pipeline, budget=budget))


def run(script: dict[float, tuple[Intent, ...]], **kw: Any) -> tuple[BacktestResult, Scripted]:
    strategy = Scripted(script)
    result = BacktestEngine.run(
        config("S"), data(), runtime(S=strategy), context_factory, schedule=RunSchedule.of(), **kw
    )
    return result, strategy


def trades(result: BacktestResult) -> list[tuple[float, Decimal, Decimal]]:
    return [
        (t.timestamp, t.quantity, t.price)
        for t in result.state.portfolio.ledger.transactions
        if t.type in (TransactionType.BUY, TransactionType.SELL)
    ]


def statuses(result: BacktestResult) -> list[tuple[float, OrderStatus]]:
    return [(o.created_at, o.status) for o in result.state.oms.orders.orders()]


# --------------------------------------------------------------------------- #
# Withdrawing, replacing, and what a target did without it
# --------------------------------------------------------------------------- #


def test_a_strategy_withdraws_its_resting_bid_and_nothing_fills() -> None:
    result, strategy = run({2.0: (target("100", 2.0, BID),), 3.0: (cancel(3.0),)})
    assert trades(result) == []
    assert statuses(result) == [(2.0, OrderStatus.CANCELLED)]
    # It hears the cancellation as it hears any order's end.
    assert (3.0, "cancelled") in strategy.heard
    # What the order held is freed: its reservation and its contributions.
    allocation = result.state.allocation
    assert dict(allocation.reservations) == {}
    assert dict(allocation.contributions) == {}
    assert allocation.notional_allocated == 0
    # The pipeline carries the cancel out; it never reaches allocation as an order.
    from alphalab.allocation.events import AllocationRejected

    assert not [e for e in allocation.events if isinstance(e, AllocationRejected)]


def test_without_a_cancel_a_new_target_trades_against_the_strategys_own_resting_order() -> None:
    # The behaviour a cancel exists for, unchanged for a run that asks for none:
    # the resting buy counts as held, the target of zero sells 100 short, and
    # the stale buy fills at 90 later.
    result, _ = run({2.0: (target("100", 2.0, BID),), 3.0: (target("0", 3.0),)})
    assert trades(result) == [(3.0, D("-100.000000"), D("100.00")), (4.0, D("100.000000"), D("90"))]


def test_a_cancel_and_a_new_limit_in_one_return_replace_the_quote() -> None:
    replaced = (cancel(3.0), target("100", 3.0, OrderTerms.limit(D("95"))))
    result, _ = run({2.0: (target("100", 2.0, BID),), 3.0: replaced})
    assert statuses(result) == [(2.0, OrderStatus.CANCELLED), (3.0, OrderStatus.FILLED)]
    assert trades(result) == [(4.0, D("100.000000"), D("95"))]


def test_a_cancel_with_nothing_working_asks_for_nothing() -> None:
    result, _ = run({3.0: (cancel(3.0),), 4.0: (cancel(4.0),)})
    assert trades(result) == [] and statuses(result) == []
    assert not [e for e in result.state.strategy.events if isinstance(e, CancelRefused)]


def test_a_filled_order_is_not_undone_by_a_cancel_at_its_own_event() -> None:
    # The bid fills at 4.0, before the strategies are asked at 4.0: the cancel
    # finds nothing working.
    result, _ = run({2.0: (target("100", 2.0, BID),), 4.0: (cancel(4.0),)})
    assert trades(result) == [(4.0, D("100.000000"), D("90"))]


# --------------------------------------------------------------------------- #
# What is not the strategy's alone to withdraw
# --------------------------------------------------------------------------- #


def test_an_order_netted_with_another_strategys_is_left_working_and_recorded() -> None:
    first = Scripted({2.0: (target("50", 2.0, BID, "S1"),), 3.0: (cancel(3.0, "S1"),)})
    second = Scripted({2.0: (target("50", 2.0, BID, "S2"),)})
    result = BacktestEngine.run(
        config("S1", "S2"),
        data(),
        runtime(S1=first, S2=second),
        context_factory,
        schedule=RunSchedule.of(),
    )
    (refused,) = [e for e in result.state.strategy.events if isinstance(e, CancelRefused)]
    assert refused.strategy_id == "S1" and "netted with another strategy's" in refused.reason
    assert trades(result) == [(4.0, D("100.000000"), D("90"))]


class AlgorithmicCanceller(algorithms.Orders):
    """Sells through a TWAP at minute 0, and asks to cancel at minute 2."""

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.bar.timestamp == algorithms.at(2):
            return (
                Intent(
                    "S",
                    algorithms.ACME,
                    D("0"),
                    timestamp=event.bar.timestamp,
                    kind=IntentKind.CANCEL,
                ),
            )
        return super().on_bar(context, event)


def test_a_parent_an_execution_plan_works_is_left_working_and_recorded() -> None:
    from dataclasses import replace

    strategy = AlgorithmicCanceller("S", algorithms.BUY_100["S"])
    state = register_strategy(create_runtime(), "S", strategy)
    entry = state.strategies["S"]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    result = BacktestEngine.run(
        algorithms.config(),
        algorithms.bars(),
        replace(state, strategies={"S": entry}),
        context_factory,
        schedule=RunSchedule(),
        execution=algorithms.TWAP_PLAN,
    )
    (refused,) = [e for e in result.state.strategy.events if isinstance(e, CancelRefused)]
    assert "execution plan works" in refused.reason
    (parent,) = result.state.oms.orders.orders()
    assert parent.filled_quantity == D("100")


# --------------------------------------------------------------------------- #
# A queue model, a malformed instruction, and replay
# --------------------------------------------------------------------------- #


def test_a_cancelled_bid_leaves_the_queue_model_holding_nothing_for_it() -> None:
    result, _ = run({2.0: (target("100", 2.0, BID),), 3.0: (cancel(3.0),)}, queue=PriceTimeQueue())
    assert trades(result) == []
    assert not result.queue.positions  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"target": D("5")}, "target must be 0"),
        ({"strength": D("0.5")}, "strength must be 1"),
        ({"terms": BID}, "states no order terms"),
    ],
)
def test_a_malformed_cancel_is_refused_as_a_malformed_intent(
    changes: dict[str, Any], message: str
) -> None:
    from dataclasses import replace

    with pytest.raises(InvalidIntentError, match=message):
        validate_intent(replace(cancel(3.0), **changes))


def test_a_cancel_replays_and_restores_from_every_pause() -> None:
    script = {
        2.0: (target("100", 2.0, BID),),
        3.0: (cancel(3.0), target("100", 3.0, OrderTerms.limit(D("95")))),
    }
    cfg, dataset = config("S"), data()
    whole = ReplayBacktest.run(cfg, dataset, runtime(S=Scripted(script)), context_factory)
    backtest = BacktestEngine.run(
        cfg, dataset, runtime(S=Scripted(script)), context_factory, schedule=RunSchedule.of()
    )
    assert whole.backtest.fills == backtest.fills
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg,
            dataset,
            runtime(S=Scripted(script)),
            context_factory,
            pause_when=_at(boundary),
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": Scripted(script)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, dataset)
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference


def _at(boundary: int) -> Any:
    def pause(progress: Any) -> bool:
        return bool(progress.records_replayed == boundary)

    return pause
