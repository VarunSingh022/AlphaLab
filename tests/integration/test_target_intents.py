"""A strategy can say what it wants to hold, and allocation trades the difference (FEA-001).

Until v3.11 an :class:`~alphalab.strategy.events.Intent` was an order delta and
nothing else, so a strategy that wanted to *hold* 100 shares had to know what it
held, what was still working, and how its fills had been divided with every
other strategy trading the same instrument -- none of which the canonical path
told it. The two target kinds put that arithmetic where the facts are:
allocation keeps each strategy's own position (its share of every fill of an
order it contributed to), measures a target against it plus the strategy's
share of what is still working, rounds the difference toward zero onto the
instrument's declared lot grid, and refuses an order below its declared minimum
notional rather than scaling it up.
"""

from __future__ import annotations

import decimal
import hashlib
import random
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation import AllocationEngine
from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.events import AllocationRejected
from alphalab.allocation.exceptions import SizingRefusedError
from alphalab.common.ids import id_scope
from alphalab.common.order_terms import MARKET, OrderTerms
from alphalab.conventions.lot import LotSpecification
from alphalab.core.enums import AssetType
from alphalab.instrument.economics import InstrumentEconomics, SettlementStyle
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry
from alphalab.persistence import deserialize, serialize
from alphalab.runtime import execution_pipeline as pipeline_module
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineResult,
    ExecutionPipelineState,
)
from alphalab.runtime.snapshot import RuntimeObjects, capture, from_primitives, restore
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import FillEvent, Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    START_CASH,
    context_factory,
    pipeline_config,
    quote,
    registry_of,
)

ASSET = "3f2b8c1e-1b1a-4c5e-9d2f-0a6b7c8d9e10"
ZERO = Decimal("0")
QUANTITY = IntentKind.TARGET_QUANTITY
WEIGHT = IntentKind.TARGET_WEIGHT


class _Targets(BaseStrategy):
    """Emits what its plan holds for each quote's instant, and records its fills."""

    def __init__(self, plan: Mapping[float, tuple[Intent, ...]]) -> None:
        self.plan = dict(plan)
        self.fills: list[FillEvent] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self.plan.get(event.quote.timestamp, ())

    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        self.fills.append(event)
        return ()


def _intent(
    strategy: str,
    target: str,
    kind: IntentKind = QUANTITY,
    *,
    asset: str = ASSET,
    strength: str = "1",
    terms: OrderTerms = MARKET,
) -> Intent:
    return Intent(strategy, asset, Decimal(target), Decimal(strength), terms=terms, kind=kind)


def _runtime(strategies: Mapping[str, BaseStrategy]) -> RuntimeState:
    runtime = create_runtime()
    for strategy_id, strategy in strategies.items():
        runtime = register_strategy(runtime, strategy_id, strategy)
    running = {}
    for strategy_id in strategies:
        entry = runtime.strategies[strategy_id]
        entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
        entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
        entry, _ = RuntimeSupervisor.start(entry, 1.3)
        running[strategy_id] = entry
    return replace(runtime, strategies=running)


def _pipeline(
    plans: Mapping[str, Mapping[float, tuple[Intent, ...]]],
    *,
    registry: InstrumentRegistry | None = None,
    shorting: bool = True,
) -> ExecutionPipelineState:
    config = replace(
        pipeline_config("A"),
        budget=CapitalBudget(
            global_capital=START_CASH,
            maximum_exposure=Decimal("10000000"),
            strategy_budgets=dict.fromkeys(plans, START_CASH),
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=shorting, enforce_integer_quantities=False
        ),
        instruments=registry,
    )
    strategies = {strategy_id: _Targets(plan) for strategy_id, plan in plans.items()}
    return ExecutionPipeline.initialize(config, _runtime(strategies), 1.0)


def _step(
    state: ExecutionPipelineState, at: float, price: str, asset: str = ASSET
) -> ExecutionPipelineResult:
    return ExecutionPipeline.process_quote(state, quote(asset, at, Decimal(price)), context_factory)


def _orders(result: ExecutionPipelineResult) -> list[tuple[str, Decimal]]:
    return [(request.side.value, request.quantity) for request in result.order_requests]


def _held(state: ExecutionPipelineState, strategy: str, asset: str = ASSET) -> Decimal:
    return AllocationEngine.strategy_position(state.allocation, strategy, asset)


def _account(state: ExecutionPipelineState, asset: str = ASSET) -> Decimal:
    position = state.portfolio.positions.get(asset)
    return ZERO if position is None else position.quantity


def _refusals(state: ExecutionPipelineState) -> list[str]:
    return [
        event.reason for event in state.allocation.events if isinstance(event, AllocationRejected)
    ]


def _economics(lot: str | None = None, minimum_notional: str | None = None) -> InstrumentEconomics:
    return InstrumentEconomics(
        Decimal("1"),
        SettlementStyle.CASH_EQUITY,
        lot=None if lot is None else LotSpecification(Decimal(lot), Decimal(lot)),
        minimum_notional=None if minimum_notional is None else Decimal(minimum_notional),
    )


def _declared(economics: InstrumentEconomics) -> tuple[str, InstrumentRegistry]:
    record = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD", economics=economics)
    return record.asset_id, registry_of(record)


# --------------------------------------------------------------------------- #
# A target is reached, and then asks for nothing
# --------------------------------------------------------------------------- #


def test_a_target_quantity_trades_the_difference_and_then_nothing() -> None:
    state = _pipeline(
        {
            "A": {
                2.0: (_intent("A", "100"),),
                3.0: (_intent("A", "100"),),
                4.0: (_intent("A", "40"),),
                5.0: (_intent("A", "-20"),),
                6.0: (_intent("A", "0"),),
            }
        }
    )

    trades = []
    for at in (2.0, 3.0, 4.0, 5.0, 6.0):
        result = _step(state, at, "100")
        state = result.state
        trades.append(_orders(result))
        assert _held(state, "A") == _account(state)

    assert trades == [
        [("buy", Decimal("100"))],
        [],  # the same target again: already held
        [("sell", Decimal("60"))],
        [("sell", Decimal("60"))],  # through flat to a short
        [("buy", Decimal("20"))],  # a target of zero closes
    ]
    assert _held(state, "A") == ZERO


def test_a_target_weight_is_a_fraction_of_the_strategys_capital_at_the_current_price() -> None:
    state = _pipeline(
        {"A": {2.0: (_intent("A", "0.1", WEIGHT),), 3.0: (_intent("A", "0.1", WEIGHT),)}}
    )

    first = _step(state, 2.0, "100")
    # 10% of 1,000,000 at 100.
    assert _orders(first) == [("buy", Decimal("1000"))]
    second = _step(first.state, 3.0, "125")
    # The same weight at 125 is 800 units: the price moved, so the holding does.
    assert _orders(second) == [("sell", Decimal("200"))]
    assert _held(second.state, "A") == Decimal("800")


def test_strength_scales_a_target() -> None:
    state = _pipeline({"A": {2.0: (_intent("A", "100", strength="0.25"),)}})

    assert _orders(_step(state, 2.0, "100")) == [("buy", Decimal("25"))]


def test_a_fractional_difference_is_rounded_toward_zero_never_past_the_target() -> None:
    state = _pipeline(
        {
            "A": {
                2.0: (_intent("A", "0.0001234567", WEIGHT),),
                3.0: (_intent("A", "-0.0001234567", WEIGHT),),
            }
        }
    )

    first = _step(state, 2.0, "3")
    ((side, bought),) = _orders(first)
    # 1,000,000 x 0.0001234567 / 3 = 41.1522333...: six places, toward zero.
    assert (side, bought) == ("buy", Decimal("41.152233"))
    second = _step(first.state, 3.0, "3")
    ((side, sold),) = _orders(second)
    assert (side, sold) == ("sell", Decimal("82.304466"))
    assert _held(second.state, "A") == Decimal("-41.152233")


# --------------------------------------------------------------------------- #
# The declared grid
# --------------------------------------------------------------------------- #


def test_a_target_is_rounded_toward_zero_onto_the_declared_lot() -> None:
    asset, registry = _declared(_economics(lot="100"))
    plan = {
        2.0: (_intent("A", "250", asset=asset),),
        3.0: (_intent("A", "250", asset=asset),),
        4.0: (_intent("A", "-30", asset=asset),),
        5.0: (_intent("A", "60", asset=asset),),
    }
    state = _pipeline({"A": plan}, registry=registry)

    trades = []
    for at in (2.0, 3.0, 4.0, 5.0):
        result = _step(state, at, "10", asset)
        state = result.state
        trades.append(_orders(result))

    assert trades == [
        [("buy", Decimal("200"))],  # 250 is 2.5 lots: two
        [],  # 50 short of the target is less than a lot
        [("sell", Decimal("200"))],  # -230 toward zero is two lots
        [],  # +60 is less than a lot
    ]
    assert _held(state, "A", asset) == ZERO


def test_a_delta_that_is_not_whole_lots_is_refused_rather_than_rounded() -> None:
    asset, registry = _declared(_economics(lot="100"))
    state = _pipeline(
        {"A": {2.0: (_intent("A", "150", IntentKind.DELTA, asset=asset),)}}, registry=registry
    )

    result = _step(state, 2.0, "10", asset)

    assert result.order_requests == ()
    (reason,) = _refusals(result.state)
    assert "not a whole number of its 100 lots" in reason


def test_a_target_below_the_minimum_notional_is_refused_not_scaled_up() -> None:
    asset, registry = _declared(_economics(minimum_notional="10000"))
    state = _pipeline(
        {"A": {2.0: (_intent("A", "50", asset=asset),), 3.0: (_intent("A", "150", asset=asset),)}},
        registry=registry,
    )

    first = _step(state, 2.0, "100", asset)
    assert first.order_requests == ()
    (reason,) = _refusals(first.state)
    assert "below the 10000 minimum" in reason and "worth 5000" in reason
    # Worth 15,000, above the minimum: traded.
    assert _orders(_step(first.state, 3.0, "100", asset)) == [("buy", Decimal("150"))]


def test_an_undeclared_grid_admits_any_quantity_as_before() -> None:
    asset, registry = _declared(_economics())
    state = _pipeline(
        {"A": {2.0: (_intent("A", "3.5", IntentKind.DELTA, asset=asset),)}}, registry=registry
    )

    assert _orders(_step(state, 2.0, "10", asset)) == [("buy", Decimal("3.5"))]


# --------------------------------------------------------------------------- #
# Several strategies, one instrument
# --------------------------------------------------------------------------- #


def test_two_strategies_each_reach_their_own_target_through_one_netted_order() -> None:
    state = _pipeline(
        {
            "A": {2.0: (_intent("A", "100"),), 3.0: (_intent("A", "100"),)},
            "B": {2.0: (_intent("B", "-40"),), 3.0: (_intent("B", "-40"),)},
        }
    )

    first = _step(state, 2.0, "100")
    # One order, the net of the two targets; each strategy holds its own.
    assert _orders(first) == [("buy", Decimal("60"))]
    assert (_held(first.state, "A"), _held(first.state, "B")) == (Decimal("100"), Decimal("-40"))
    assert _account(first.state) == Decimal("60")
    # Both targets again: neither strategy asks for anything.
    assert _orders(_step(first.state, 3.0, "100")) == []


def test_one_strategys_target_is_not_disturbed_by_another_strategys_trading() -> None:
    state = _pipeline(
        {
            "A": {2.0: (_intent("A", "100"),), 4.0: (_intent("A", "100"),)},
            "B": {3.0: (_intent("B", "30", IntentKind.DELTA),)},
        }
    )

    for at in (2.0, 3.0):
        state = _step(state, at, "100").state
    assert _account(state) == Decimal("130")

    # The account holds 130, but A holds its 100: its target asks for nothing.
    assert _orders(_step(state, 4.0, "100")) == []


def test_a_strategys_position_is_the_sum_of_the_fills_it_was_told_of() -> None:
    """Two computations, one rule: the fill feedback and the position agree."""

    plan_a = {2.0: (_intent("A", "70"),), 3.0: (_intent("A", "-10"),), 4.0: (_intent("A", "25"),)}
    plan_b = {2.0: (_intent("B", "-30"),), 3.0: (_intent("B", "45"),), 4.0: (_intent("B", "45"),)}
    state = _pipeline({"A": plan_a, "B": plan_b})
    for at in (2.0, 3.0, 4.0):
        state = _step(state, at, "100").state

    for strategy_id, target in (("A", Decimal("25")), ("B", Decimal("45"))):
        instance = state.strategy.strategies[strategy_id].instance
        assert isinstance(instance, _Targets)
        told = sum((fill.attributed_quantity or ZERO for fill in instance.fills), start=ZERO)
        assert told == _held(state, strategy_id) == target


# --------------------------------------------------------------------------- #
# What is still working counts
# --------------------------------------------------------------------------- #


def test_a_working_order_toward_the_target_is_not_asked_for_twice() -> None:
    resting = OrderTerms.limit(Decimal("90"))
    state = _pipeline(
        {
            "A": {
                2.0: (_intent("A", "100", terms=resting),),
                3.0: (_intent("A", "100", terms=resting),),
                4.0: (_intent("A", "150", terms=resting),),
            }
        }
    )

    first = _step(state, 2.0, "100")
    assert _orders(first) == [("buy", Decimal("100"))]
    assert _held(first.state, "A") == ZERO, "the limit rests below the market"
    # Working toward the target already: nothing more.
    second = _step(first.state, 3.0, "100")
    assert _orders(second) == []
    # A higher target asks only for the rest.
    assert _orders(_step(second.state, 4.0, "100")) == [("buy", Decimal("50"))]


def test_the_working_share_is_computed_only_when_a_target_is_asked_for() -> None:
    state = _pipeline({"A": {}})
    deltas = (_intent("A", "1", IntentKind.DELTA),)

    assert pipeline_module._working_shares(state, deltas) is pipeline_module._NO_WORKING
    assert pipeline_module._instrument_grid(state, deltas) == (
        pipeline_module._NO_LOTS,
        pipeline_module._NO_MINIMUMS,
    )


# --------------------------------------------------------------------------- #
# Honest about runs that never recorded positions
# --------------------------------------------------------------------------- #


def test_a_run_whose_positions_were_never_recorded_refuses_a_target_and_trades_a_delta() -> None:
    state = _pipeline({"A": {2.0: (_intent("A", "100"), _intent("A", "5", IntentKind.DELTA))}})
    upgraded = replace(state, allocation=replace(state.allocation, strategy_positions=None))

    result = _step(upgraded, 2.0, "100")

    assert _orders(result) == [("buy", Decimal("5"))]
    (reason,) = _refusals(result.state)
    assert "per-strategy positions were not recorded" in reason
    # And nothing starts recording them part-way through: a fill is not a book.
    assert result.state.allocation.strategy_positions is None
    with pytest.raises(SizingRefusedError, match="state a delta instead"):
        AllocationEngine.strategy_position(result.state.allocation, "A", ASSET)


def test_a_target_without_a_positive_price_is_recorded_and_the_rest_sized() -> None:
    other = "0b7c5a52-8f0e-4d8e-b8a6-5f3c2d1e0f9a"
    allocation, requests = AllocationEngine.allocate(
        _pipeline({"A": {}}).allocation,
        (_intent("A", "1"), _intent("A", "2", asset=other)),
        {ASSET: ZERO, other: Decimal("10")},
        pipeline_config("A").sizing_model,
        AllocationConstraints(allow_shorting=True),
        2.0,
    )

    assert [(request.asset_id, request.quantity) for request in requests] == [(other, Decimal("2"))]
    (reason,) = [e.reason for e in allocation.events if isinstance(e, AllocationRejected)]
    assert "no positive price" in reason


# --------------------------------------------------------------------------- #
# Persistence and determinism
# --------------------------------------------------------------------------- #


def test_each_strategys_position_survives_a_snapshot_and_the_target_still_holds() -> None:
    plans = {
        "A": {2.0: (_intent("A", "100"),), 3.0: (_intent("A", "100"),)},
        "B": {2.0: (_intent("B", "-40"),), 3.0: (_intent("B", "-40"),)},
    }
    state = _step(_pipeline(plans), 2.0, "100").state

    objects = RuntimeObjects(
        sizing_model=state.config.sizing_model,
        simulator=state.config.simulator,
        strategies={sid: state.strategy.strategies[sid].instance for sid in plans},
    )
    restored = restore(from_primitives(deserialize(serialize(capture(state)))), objects)

    assert restored.allocation.strategy_positions == state.allocation.strategy_positions
    assert _orders(_step(restored, 3.0, "100")) == []


def _random_run(seed: int, context: decimal.Context | None = None) -> ExecutionPipelineState:
    """Forty steps of three strategies mixing deltas, target quantities and weights.

    The inputs are built in the default context; only the run is played in
    ``context``, so a difference is the library's and never the test's.
    """

    rng = random.Random(seed)
    plans: dict[str, dict[float, tuple[Intent, ...]]] = {"A": {}, "B": {}, "C": {}}
    for index in range(40):
        at = 2.0 + index
        for strategy_id, plan in plans.items():
            if rng.random() < 0.6:
                kind = rng.choice((QUANTITY, WEIGHT, IntentKind.DELTA))
                scale = "0.001" if kind is WEIGHT else "1"
                target = Decimal(rng.randint(-200, 200)) * Decimal(scale)
                plan[at] = (_intent(strategy_id, str(target), kind),)
    prices = [str(Decimal(100) + Decimal(rng.randint(-500, 500)) / 100) for _ in range(40)]
    with id_scope(seed), decimal.localcontext(context or decimal.getcontext()):
        state = _pipeline(plans)
        for index, price in enumerate(prices):
            state = _step(state, 2.0 + index, price).state
    return state


def _digest(state: ExecutionPipelineState) -> str:
    # Compared as a digest: a difference is a failure either way, and pytest's
    # diff of two whole serialized runs takes minutes.
    return hashlib.sha256(serialize(capture(state)).encode()).hexdigest()


@pytest.mark.parametrize("seed", [3, 11, 2024])
def test_strategy_positions_always_sum_to_the_account_and_a_run_repeats_exactly(
    seed: int,
) -> None:
    state = _random_run(seed)

    positions = state.allocation.strategy_positions
    assert positions is not None
    total = sum(
        (AllocationEngine.strategy_position(state.allocation, sid, ASSET) for sid in "ABC"),
        start=ZERO,
    )
    assert total == _account(state)
    assert _digest(_random_run(seed)) == _digest(state)


def test_positions_do_not_depend_on_the_callers_decimal_context() -> None:
    hostile = decimal.Context(prec=5, rounding=decimal.ROUND_UP)

    assert _digest(_random_run(7, hostile)) == _digest(_random_run(7))
