"""A strategy exercises its own options from its hooks (v4.2.0, ledger FUT-014).

A hook returns ``Intent(kind=IntentKind.EXERCISE, instrument=<option>,
target=<contracts>)``. It never reaches allocation, risk or the OMS: it takes
effect at the instant of the event whose hook returned it, at the end of that
event's step, through the checks a scheduled ``Exercise`` passes, against the
asking strategy's own contracts only. A cash-settled contract settles against
the last price the run observed for its underlying. What the run cannot carry
out is an ``ExerciseRefused`` in the strategy runtime's log; nothing moves and
the strategy keeps running. Backtest and replay agree, across a pause.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
    ScheduledExercise,
    ScheduledExpiry,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import OrderTerms
from alphalab.lifecycle import digest_run
from alphalab.options.enums import OptionType
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import Exercise, OptionExpiry
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import ExerciseRefused, Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState, StrategyStatus
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import START_CASH, context_factory
from tests.integration.test_option_lifecycle import (
    AM_CALL,
    AM_PUT,
    EU_CASH_CALL,
    EXPIRES,
    SHARE,
    SHARE_MIDS,
    config,
    data,
    series,
)

Plan = Mapping[tuple[float, str], tuple[Intent, ...]]


class Planner(BaseStrategy):
    """Returns what its plan holds for each ``(instant, asset)`` it is quoted at; stateless."""

    def __init__(self, plan: Plan) -> None:
        self._plan = dict(plan)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._plan.get((event.quote.timestamp, event.quote.asset_id), ())


def hold(sid: str, asset_id: str, quantity: str, at: float = 2.0) -> Intent:
    return Intent(sid, asset_id, Decimal(quantity), timestamp=at, kind=IntentKind.TARGET_QUANTITY)


def exercise(sid: str, asset_id: str, contracts: str, at: float, **fields: Any) -> Intent:
    return Intent(
        sid, asset_id, Decimal(contracts), timestamp=at, kind=IntentKind.EXERCISE, **fields
    )


def strategies(plans: Mapping[str, Plan]) -> RuntimeState:
    state = create_runtime()
    for sid, plan in plans.items():
        state = register_strategy(state, sid, Planner(plan))
    running = {}
    for sid, entry in state.strategies.items():
        entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
        entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
        running[sid], _ = RuntimeSupervisor.start(entry, 1.3)
    return RuntimeState(strategies=running)


def inputs(contract: Any) -> Any:
    return data({SHARE.asset_id: SHARE_MIDS, contract.asset_id: series("5", "6", "8", "9")})


def schedule_for(contract: Any, exercises: Iterable[ScheduledExercise] = ()) -> RunSchedule:
    """The contract's expiry, out of the money -- whatever is still held lapses -- and exercises."""

    lapses = "112" if contract.terms.option_type is OptionType.PUT else "90"
    return RunSchedule.of(
        exercises=list(exercises),
        expiries=[ScheduledExpiry(OptionExpiry(contract.asset_id, Decimal(lapses)), EXPIRES)],
    )


def run(
    contract: Any,
    plans: Mapping[str, Plan],
    exercises: Iterable[ScheduledExercise] = (),
    **kw: Any,
) -> BacktestResult:
    return BacktestEngine.run(
        config((contract, SHARE), tuple(plans), **kw),
        inputs(contract),
        strategies(plans),
        context_factory,
        schedule=schedule_for(contract, exercises),
    )


def cash(result: BacktestResult) -> Decimal:
    return result.state.portfolio.cash.balance("USD")


def held(result: BacktestResult, asset_id: str) -> Decimal:
    position = result.state.portfolio.positions.get(asset_id)
    return Decimal("0") if position is None else position.quantity


def refusals(result: BacktestResult) -> list[ExerciseRefused]:
    return [e for e in result.state.strategy.events if isinstance(e, ExerciseRefused)]


def share_of(result: BacktestResult, sid: str, asset_id: str) -> Decimal:
    from alphalab.allocation.engine import AllocationEngine

    return dict(AllocationEngine.shares_of(result.state.allocation, asset_id)).get(sid, Decimal(0))


def identity(result: BacktestResult) -> None:
    valuation = result.valuation
    assert valuation.equity == (
        START_CASH + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )


# --------------------------------------------------------------------------- #
# What an exercise from a hook does
# --------------------------------------------------------------------------- #


def test_a_hook_exercises_a_call_into_the_shares_at_its_own_instant() -> None:
    plans = {
        "S": {
            (2.0, AM_CALL.asset_id): (hold("S", AM_CALL.asset_id, "2"),),
            (4.0, AM_CALL.asset_id): (exercise("S", AM_CALL.asset_id, "2", 4.0),),
        }
    }
    result = run(AM_CALL, plans)

    assert held(result, AM_CALL.asset_id) == 0
    assert held(result, SHARE.asset_id) == Decimal("200")
    premium = Decimal("5") * 100 * 2
    assert cash(result) == START_CASH - premium - Decimal("100") * 100 * 2
    (record,) = [r for r in result.state.trade_records if r.trade_id.startswith("EXERCISE")]
    assert record.trade_id == f"EXERCISE-{AM_CALL.asset_id}-4.0-S-0"
    # Effective at the instant of the quote whose hook asked for it.
    booked = {
        t.timestamp
        for t in result.state.portfolio.ledger.transactions
        if t.asset_id == AM_CALL.asset_id
    }
    assert booked == {2.0, 4.0}
    assert not refusals(result)
    identity(result)


def test_a_hook_exercise_equals_the_same_exercise_scheduled() -> None:
    held_two = {"S": {(2.0, AM_CALL.asset_id): (hold("S", AM_CALL.asset_id, "2"),)}}
    scheduled = run(
        AM_CALL, held_two, [ScheduledExercise(Exercise(AM_CALL.asset_id, Decimal("1")), 4.0)]
    )
    asked = run(
        AM_CALL,
        {
            "S": {
                **held_two["S"],
                (4.0, AM_CALL.asset_id): (exercise("S", AM_CALL.asset_id, "1", 4.0),),
            }
        },
    )
    assert cash(asked) == cash(scheduled)
    assert held(asked, SHARE.asset_id) == held(scheduled, SHARE.asset_id) == Decimal("100")
    assert asked.valuation == scheduled.valuation


def test_a_cash_settled_exercise_settles_against_the_last_observed_underlying_price() -> None:
    plans = {
        "S": {
            (2.0, EU_CASH_CALL.asset_id): (hold("S", EU_CASH_CALL.asset_id, "2"),),
            # 6.0 is the last trading instant: the European window opens.
            (6.0, SHARE.asset_id): (exercise("S", EU_CASH_CALL.asset_id, "2", 6.0),),
        }
    }
    result = run(EU_CASH_CALL, plans)
    premium = Decimal("5") * 100 * 2
    underlying = Decimal(SHARE_MIDS[6.0])  # what the run had seen at 6.0
    paid = (underlying - Decimal("100")) * 100 * 2
    assert cash(result) == START_CASH - premium + paid
    assert held(result, EU_CASH_CALL.asset_id) == 0
    assert not refusals(result)
    identity(result)


def test_a_partial_exercise_leaves_the_rest_of_the_position() -> None:
    plans = {
        "S": {
            (2.0, AM_CALL.asset_id): (hold("S", AM_CALL.asset_id, "3"),),
            (3.0, AM_CALL.asset_id): (exercise("S", AM_CALL.asset_id, "1", 3.0),),
            (4.0, AM_CALL.asset_id): (exercise("S", AM_CALL.asset_id, "1", 4.0),),
        }
    }
    result = run(AM_CALL, plans)
    # Two exercised; the third lapses at the out-of-the-money expiry.
    assert held(result, SHARE.asset_id) == Decimal("200")
    exercised = [r for r in result.state.trade_records if r.trade_id.startswith("EXERCISE")]
    assert [r.trade_id for r in exercised] == [
        f"EXERCISE-{AM_CALL.asset_id}-3.0-S-0",
        f"EXERCISE-{AM_CALL.asset_id}-4.0-S-0",
    ]
    premium = Decimal("5") * 100 * 3
    assert cash(result) == START_CASH - premium - Decimal("100") * 100 * 2
    identity(result)


def test_a_strategy_exercises_its_own_contracts_and_no_other_strategys() -> None:
    plans = {
        "A": {
            (2.0, AM_CALL.asset_id): (hold("A", AM_CALL.asset_id, "2"),),
            (4.0, AM_CALL.asset_id): (exercise("A", AM_CALL.asset_id, "1", 4.0),),
        },
        "B": {(2.0, AM_CALL.asset_id): (hold("B", AM_CALL.asset_id, "2"),)},
    }
    result = run(AM_CALL, plans)
    # A's one contract became A's hundred shares; B's two were not touched, and
    # lapse with A's other one at the out-of-the-money expiry.
    assert share_of(result, "A", SHARE.asset_id) == Decimal("100")
    assert share_of(result, "B", SHARE.asset_id) == Decimal("0")
    (record,) = [r for r in result.state.trade_records if r.trade_id.startswith("EXERCISE")]
    assert [(c.strategy_id, c.quantity) for c in record.contributions] == [("A", Decimal("1"))]


# --------------------------------------------------------------------------- #
# Refused, recorded, and nothing moves
# --------------------------------------------------------------------------- #


def _refused_run(contract: Any, plans: Mapping[str, Plan], **kw: Any) -> tuple[Any, Any]:
    result = run(contract, plans, **kw)

    def orders_only(value: tuple[Intent, ...]) -> tuple[Intent, ...]:
        return tuple(i for i in value if i.kind is not IntentKind.EXERCISE)

    baseline_plans = {
        sid: {key: orders_only(value) for key, value in plan.items()} for sid, plan in plans.items()
    }
    return result, run(contract, baseline_plans, **kw)


@pytest.mark.parametrize(
    ("contract", "plan", "named"),
    [
        (
            AM_CALL,
            {
                (2.0, AM_CALL.asset_id): (hold("S", AM_CALL.asset_id, "2"),),
                (4.0, AM_CALL.asset_id): (exercise("S", AM_CALL.asset_id, "3", 4.0),),
            },
            "holds 2",
        ),
        (
            EU_CASH_CALL,
            {
                (2.0, EU_CASH_CALL.asset_id): (hold("S", EU_CASH_CALL.asset_id, "2"),),
                (4.0, EU_CASH_CALL.asset_id): (exercise("S", EU_CASH_CALL.asset_id, "1", 4.0),),
            },
            "exercised",
        ),
        (
            AM_CALL,
            {(4.0, AM_CALL.asset_id): (exercise("S", AM_CALL.asset_id, "1", 4.0),)},
            "holds 0",
        ),
        (
            AM_CALL,
            {
                (2.0, AM_CALL.asset_id): (hold("S", AM_CALL.asset_id, "1"),),
                (4.0, AM_CALL.asset_id): (
                    Intent("S", AM_CALL.asset_id, Decimal("-1"), timestamp=4.0,
                           terms=OrderTerms.limit(Decimal("100"))),
                    exercise("S", AM_CALL.asset_id, "1", 4.0),
                ),
            },
            "working",
        ),
        (
            AM_PUT,
            {
                (2.0, AM_PUT.asset_id): (hold("S", AM_PUT.asset_id, "1"),),
                (4.0, AM_PUT.asset_id): (exercise("S", AM_PUT.asset_id, "1", 4.0),),
            },
            "short",
        ),
    ],
    ids=["beyond-own-position", "european-before-window", "nothing-held", "working-orders",
         "delivery-would-short"],
)  # fmt: skip
def test_an_exercise_the_run_cannot_carry_out_is_recorded_and_moves_nothing(
    contract: Any, plan: Plan, named: str
) -> None:
    result, baseline = _refused_run(contract, {"S": plan}, shorting=False)

    (refused,) = refusals(result)
    assert refused.strategy_id == "S" and refused.asset_id == contract.asset_id
    assert named in refused.reason
    assert result.state.strategy.strategies["S"].status is StrategyStatus.RUNNING
    # Nothing moved: the same positions and cash as the run that never asked.
    assert {a: p.quantity for a, p in result.state.portfolio.positions.items()} == {
        a: p.quantity for a, p in baseline.state.portfolio.positions.items()
    }
    assert cash(result) == cash(baseline)
    assert [(f.asset_id, f.side, f.quantity, f.price) for f in result.fills] == [
        (f.asset_id, f.side, f.quantity, f.price) for f in baseline.fills
    ]


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        ({"strength": Decimal("0.5")}, "strength must be 1"),
        ({"terms": OrderTerms.limit(Decimal("1"))}, "states no order terms"),
    ],
)
def test_a_malformed_exercise_intent_fails_the_strategy_as_any_malformed_intent_does(
    fields: dict[str, Any], named: str
) -> None:
    plan: Plan = {
        (2.0, AM_CALL.asset_id): (hold("S", AM_CALL.asset_id, "2"),),
        (4.0, AM_CALL.asset_id): (exercise("S", AM_CALL.asset_id, "1", 4.0, **fields),),
    }
    result = run(AM_CALL, {"S": plan})
    strategy = result.state.strategy.strategies["S"]
    assert strategy.status is StrategyStatus.FAILED and named in (strategy.last_error or "")
    assert held(result, SHARE.asset_id) == 0, "nothing was exercised"
    assert not [r for r in result.state.trade_records if r.trade_id.startswith("EXERCISE")]


def test_a_fractional_number_of_contracts_is_malformed() -> None:
    plan: Plan = {
        (2.0, AM_CALL.asset_id): (hold("S", AM_CALL.asset_id, "2"),),
        (4.0, AM_CALL.asset_id): (exercise("S", AM_CALL.asset_id, "0.5", 4.0),),
    }
    strategy = run(AM_CALL, {"S": plan}).state.strategy.strategies["S"]
    assert strategy.status is StrategyStatus.FAILED
    assert "whole number of contracts" in (strategy.last_error or "")


# --------------------------------------------------------------------------- #
# Replay, pause and restore
# --------------------------------------------------------------------------- #

PLANS: Mapping[str, Plan] = {
    "S": {
        (2.0, AM_CALL.asset_id): (hold("S", AM_CALL.asset_id, "3"),),
        (3.0, AM_CALL.asset_id): (exercise("S", AM_CALL.asset_id, "5", 3.0),),  # refused
        (4.0, AM_CALL.asset_id): (exercise("S", AM_CALL.asset_id, "2", 4.0),),
    }
}


def _replay(pause: int | None = None) -> Any:
    return ReplayBacktest.run(
        config((AM_CALL, SHARE), ("S",)),
        inputs(AM_CALL),
        strategies(PLANS),
        context_factory,
        schedule=schedule_for(AM_CALL),
        pause_when=lambda progress: pause is not None and progress.records_replayed == pause,
    )


def test_the_replay_equals_the_backtest_and_a_pause_anywhere_resumes_to_it() -> None:
    backtest = run(AM_CALL, PLANS)
    whole = _replay()
    assert whole.backtest.fills == backtest.fills
    assert whole.backtest.valuation == backtest.valuation
    assert len(refusals(whole.backtest)) == 1
    reference = digest_run(whole.backtest).result_id
    pipeline = config((AM_CALL, SHARE), ("S",)).pipeline
    for pause in range(1, len(inputs(AM_CALL).records)):
        paused = _replay(pause)
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                pipeline.sizing_model,
                pipeline.simulator,
                {"S": Planner(PLANS["S"])},
                pipeline.instruments,
            ),
            paused.run.config.fill_policy,
        )
        restored = restore_replay(
            replay_from_primitives(payload), objects, inputs(AM_CALL), schedule_for(AM_CALL)
        )
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference
        assert refusals(resumed.backtest) == refusals(whole.backtest)


class OnTimer(Planner):
    """Holds through its plan, and exercises from a timer -- the dispatch path."""

    def on_timer(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return (exercise("S", AM_CALL.asset_id, "1", event.timestamp),)


class OnFill(Planner):
    """Exercises one contract the moment its purchase fills -- the feedback path."""

    def on_fill(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.instrument != AM_CALL.asset_id:
            return ()
        return (exercise("S", AM_CALL.asset_id, "1", event.timestamp),)


@pytest.mark.parametrize(
    ("strategy_type", "at"),
    [(OnTimer, 3.5), (OnFill, 2.0)],
    ids=["timer", "fill-feedback"],
)
def test_an_exercise_asked_from_any_hook_takes_effect_at_that_hooks_instant(
    strategy_type: type[Planner], at: float
) -> None:
    from alphalab.strategy.events import TimerEvent

    plan: Plan = {(2.0, AM_CALL.asset_id): (hold("S", AM_CALL.asset_id, "2"),)}
    state = register_strategy(create_runtime(), "S", strategy_type(plan))
    entry, _ = RuntimeSupervisor.configure(state.strategies["S"], {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    timers = [TimerEvent("T-1", 3.5, timer_id="t1", schedule_type="once")]
    expiry = ScheduledExpiry(OptionExpiry(AM_CALL.asset_id, Decimal("90")), EXPIRES)
    result = BacktestEngine.run(
        config((AM_CALL, SHARE), ("S",)),
        inputs(AM_CALL),
        RuntimeState(strategies={"S": entry}),
        context_factory,
        schedule=RunSchedule.of(timers=timers, expiries=[expiry]),
    )
    assert held(result, SHARE.asset_id) == Decimal("100")
    assert not refusals(result)
    transactions = result.state.portfolio.ledger.transactions
    booked = sorted({t.timestamp for t in transactions if t.asset_id == SHARE.asset_id})
    assert booked == [at]


class OnTimerWithAnOrder(Planner):
    """From a timer, an order and an exercise together: each takes its own path."""

    def on_timer(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return (
            Intent("S", SHARE.asset_id, Decimal("10"), timestamp=event.timestamp),
            exercise("S", AM_CALL.asset_id, "1", event.timestamp),
        )


def test_a_timer_asking_for_an_order_and_an_exercise_gets_both() -> None:
    from alphalab.strategy.events import TimerEvent

    plan: Plan = {(2.0, AM_CALL.asset_id): (hold("S", AM_CALL.asset_id, "2"),)}
    state = register_strategy(create_runtime(), "S", OnTimerWithAnOrder(plan))
    entry, _ = RuntimeSupervisor.configure(state.strategies["S"], {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    timers = [TimerEvent("T-1", 3.5, timer_id="t1", schedule_type="once")]
    expiry = ScheduledExpiry(OptionExpiry(AM_CALL.asset_id, Decimal("90")), EXPIRES)
    result = BacktestEngine.run(
        config((AM_CALL, SHARE), ("S",)),
        inputs(AM_CALL),
        RuntimeState(strategies={"S": entry}),
        context_factory,
        schedule=RunSchedule.of(timers=timers, expiries=[expiry]),
    )
    # 100 delivered by the exercise at 3.5, 10 bought by the order, which rests
    # to the share's next quote.
    assert held(result, SHARE.asset_id) == Decimal("110")
    assert [
        r.trade_id for r in result.state.trade_records if r.trade_id.startswith("EXERCISE")
    ] == [f"EXERCISE-{AM_CALL.asset_id}-3.5-S-0"]


def test_a_cash_exercise_whose_underlying_the_run_never_priced_is_refused() -> None:
    from alphalab.core.enums import AssetType
    from alphalab.instrument.record import InstrumentRecord
    from alphalab.options.enums import ExerciseStyle
    from alphalab.options.expiration import SettlementStyle
    from tests.integration.test_option_lifecycle import option

    index = InstrumentRecord("NEVERPRICED", AssetType.INDEX, "XIDX", "USD")
    contract = option(
        "NPC100",
        OptionType.CALL,
        ExerciseStyle.AMERICAN,
        SettlementStyle.CASH,
        deliverable=index,
    )
    plan: Plan = {
        (2.0, contract.asset_id): (hold("S", contract.asset_id, "1"),),
        (4.0, contract.asset_id): (exercise("S", contract.asset_id, "1", 4.0),),
    }
    result = BacktestEngine.run(
        config((contract, SHARE, index), ("S",)),
        data({SHARE.asset_id: SHARE_MIDS, contract.asset_id: series("5", "6", "8", "9")}),
        strategies({"S": plan}),
        context_factory,
        schedule=schedule_for(contract),
    )
    (refused,) = refusals(result)
    assert "has observed no price for it" in refused.reason
    assert "never a value it supplies" in refused.reason
