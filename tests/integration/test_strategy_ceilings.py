"""A strategy's budget can be a ceiling on what it commits (ledger OFE-003).

Until v3.12 ``CapitalBudget.strategy_budgets`` was read by the sizing models and
by nothing else: a strategy could deploy more than its budget event after event
so long as the account's total fitted. ``enforce_strategy_budgets=True`` makes
each declared amount a ceiling on the capital the strategy commits -- its
positions at cost plus its working orders' reservations -- judged per strategy,
before netting, so one strategy over its ceiling cannot move another's orders.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation import AllocationEngine
from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.ceilings import StrategyCapital, adding_exposure
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.events import AllocationRejected
from alphalab.allocation.exceptions import AllocationValidationError
from alphalab.allocation.sizing import FixedQuantitySizing
from alphalab.allocation.snapshot import capture as capture_allocation
from alphalab.allocation.snapshot import from_primitives as allocation_from_primitives
from alphalab.allocation.snapshot import restore as restore_allocation
from alphalab.allocation.state import AllocationState
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.core.enums import OrderStatus
from alphalab.execution.costs import FREE
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineState
from alphalab.runtime.snapshot import RuntimeObjects, capture, from_primitives, restore
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import context_factory, pipeline_config, quote

ASSET = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
OTHER = "16fd2706-8baf-433b-82eb-8c7fada847da"
ZERO = Decimal("0")
PRICES = {ASSET: Decimal("100"), OTHER: Decimal("50")}


def _budget(**ceilings: str) -> CapitalBudget:
    return CapitalBudget(
        global_capital=Decimal("1000000"),
        maximum_exposure=Decimal("1000000"),
        strategy_budgets={name: Decimal(amount) for name, amount in ceilings.items()},
        enforce_strategy_budgets=True,
    )


def _intent(strategy_id: str, quantity: str, asset: str = ASSET, **kwargs: Any) -> Intent:
    return Intent(strategy_id, asset, Decimal(quantity), timestamp=1.0, **kwargs)


def _allocate(state: AllocationState, *intents: Intent) -> tuple[AllocationState, tuple[Any, ...]]:
    return AllocationEngine.allocate(
        state,
        intents,
        PRICES,
        FixedQuantitySizing(),
        AllocationConstraints(allow_shorting=True, enforce_integer_quantities=False),
        1.0,
    )


def _refusals(state: AllocationState) -> list[str]:
    return [event.reason for event in state.events if isinstance(event, AllocationRejected)]


def _capital(state: AllocationState, strategy_id: str) -> StrategyCapital:
    return state.strategy_capital.get(strategy_id, StrategyCapital())


# --------------------------------------------------------------------------- #
# The declaration
# --------------------------------------------------------------------------- #


def test_an_enforced_ceiling_must_be_a_finite_non_negative_amount() -> None:
    for bad in ("-1", "NaN", "Infinity"):
        with pytest.raises(AllocationValidationError, match="enforced as a ceiling"):
            _budget(A=bad)
    # Not enforced, an amount is only a sizing input and is not judged here.
    assert CapitalBudget(Decimal("1"), Decimal("1"), strategy_budgets={"A": Decimal("-1")})


def test_only_an_enforced_budget_has_ceilings_and_only_for_strategies_it_names() -> None:
    plain = CapitalBudget(Decimal("1"), Decimal("1"), strategy_budgets={"A": Decimal("1")})
    enforced = _budget(A="1")

    assert plain.strategy_ceiling("A") is None
    assert enforced.strategy_ceiling("A") == Decimal("1")
    assert enforced.strategy_ceiling("B") is None
    assert enforced.in_currency("EUR").enforce_strategy_budgets, "relabelling keeps it"


# --------------------------------------------------------------------------- #
# The arithmetic: positions at cost
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("held", "delta", "added"),
    [
        ("0", "10", "10"),
        ("0", "-10", "10"),
        ("5", "3", "3"),
        ("5", "-3", "0"),
        ("5", "-5", "0"),
        ("5", "-8", "3"),
        ("-5", "-2", "2"),
        ("-5", "9", "4"),
    ],
)
def test_only_exposure_added_is_counted(held: str, delta: str, added: str) -> None:
    assert adding_exposure(Decimal(held), Decimal(delta)) == Decimal(added)


def test_a_position_is_held_at_cost_and_reduced_in_proportion() -> None:
    capital = StrategyCapital().reserve("o1", Decimal("1000"))
    capital = capital.fill("o1", ASSET, ZERO, Decimal("10"), Decimal("102"))
    # The fill deployed 1,020; the reservation converts up to what it held.
    assert capital.deployed[ASSET] == Decimal("1020")
    assert capital.reserved == {} and capital.reserved_total == ZERO
    assert capital.committed == Decimal("1020")

    reduced = capital.fill("o2", ASSET, Decimal("10"), Decimal("-4"), Decimal("150"))
    assert reduced.deployed[ASSET] == Decimal("612"), "six tenths of what it cost, not of 150"

    crossed = reduced.fill("o3", ASSET, Decimal("6"), Decimal("-8"), Decimal("90"))
    assert crossed.deployed[ASSET] == Decimal("180"), "the old side freed, the new side at cost"

    closed = crossed.fill("o4", ASSET, Decimal("-2"), Decimal("2"), Decimal("95"))
    assert ASSET not in closed.deployed and closed.committed == ZERO


def test_a_fill_converts_only_what_it_deploys_and_the_rest_stays_reserved() -> None:
    capital = StrategyCapital().reserve("o1", Decimal("1000"))
    partly = capital.fill("o1", ASSET, ZERO, Decimal("4"), Decimal("100"))

    assert partly.reserved["o1"] == Decimal("600")
    assert partly.committed == Decimal("1000")
    assert partly.release("o1").committed == Decimal("400")
    assert partly.release("o1").release("o1") == partly.release("o1"), "released once"


# --------------------------------------------------------------------------- #
# Allocation judges each strategy against its own ceiling
# --------------------------------------------------------------------------- #


def test_an_intent_over_its_strategys_ceiling_is_refused_and_another_strategys_is_not() -> None:
    state = AllocationEngine.initialize(_budget(A="1000", B="1000000"))
    state, orders = _allocate(state, _intent("A", "8"), _intent("B", "5", OTHER))
    assert {order.asset_id for order in orders} == {ASSET, OTHER}
    assert _capital(state, "A").reserved_total == Decimal("800")

    state, orders = _allocate(state, _intent("A", "3"), _intent("B", "5", OTHER))

    assert [order.asset_id for order in orders] == [OTHER]
    (reason,) = _refusals(state)
    assert reason.startswith("A has committed 800 of its 1000 ceiling")
    assert "would commit 300 more" in reason
    assert _capital(state, "A").reserved_total == Decimal("800"), "nothing reserved for it"


def test_a_batch_is_judged_in_the_order_it_was_stated() -> None:
    state = AllocationEngine.initialize(_budget(A="1000"))
    state, orders = _allocate(state, _intent("A", "5"), _intent("A", "6", OTHER), _intent("A", "4"))

    # 500, then 300 more of OTHER fits (800), then 400 more of ASSET does not.
    assert sorted((order.asset_id, order.quantity) for order in orders) == sorted(
        [(ASSET, Decimal("5")), (OTHER, Decimal("6"))]
    )
    assert len(_refusals(state)) == 1


def test_a_reduction_commits_nothing_and_passes_at_the_ceiling() -> None:
    state = AllocationEngine.initialize(_budget(A="1000"))
    state, _ = _allocate(state, _intent("A", "10"))
    assert _capital(state, "A").committed == Decimal("1000")

    # The working buy is A's committed position: selling against it adds nothing.
    state, orders = AllocationEngine.allocate(
        state,
        (_intent("A", "-4"),),
        PRICES,
        FixedQuantitySizing(),
        AllocationConstraints(allow_shorting=True, enforce_integer_quantities=False),
        1.0,
        working={("A", ASSET): Decimal("10")},
    )

    assert [order.quantity for order in orders] == [Decimal("4")]
    assert _refusals(state) == []


def test_netting_does_not_hide_whose_exposure_an_order_adds() -> None:
    state = AllocationEngine.initialize(_budget(A="5000", B="5000"))
    state, (order,) = _allocate(state, _intent("A", "10"), _intent("B", "-4"))

    assert order.quantity == Decimal("6"), "the venue sees the net"
    # Each strategy will hold its own quantity, so each commits its own exposure.
    assert _capital(state, "A").reserved == {order.order_id: Decimal("1000")}
    assert _capital(state, "B").reserved == {order.order_id: Decimal("400")}
    assert state.reservations[order.order_id] == Decimal("600"), "the account commits the net"


def test_a_strategy_without_a_ceiling_and_a_budget_without_enforcement_keep_no_ledger() -> None:
    state = AllocationEngine.initialize(_budget(A="1000"))
    state, _ = _allocate(state, _intent("B", "1000"))
    assert state.strategy_capital == {}

    plain = CapitalBudget(
        Decimal("1000000"), Decimal("1000000"), strategy_budgets={"A": Decimal("1")}
    )
    unenforced, orders = _allocate(AllocationEngine.initialize(plain), _intent("A", "1000"))
    assert len(orders) == 1 and unenforced.strategy_capital == {}


def test_retiring_an_order_releases_what_it_reserved_for_each_strategy() -> None:
    state = AllocationEngine.initialize(_budget(A="5000", B="5000"))
    state, (order,) = _allocate(state, _intent("A", "10"), _intent("B", "-4"))

    retired = AllocationEngine.retire_contributions(state, order.order_id)

    assert _capital(retired, "A").committed == ZERO
    assert _capital(retired, "B").committed == ZERO


def test_the_ledger_and_the_budget_currency_survive_an_allocation_snapshot() -> None:
    budget = replace(_budget(A="5000"), currency="EUR")
    state = AllocationEngine.initialize(budget)
    state, (order,) = _allocate(state, _intent("A", "10"))
    state = AllocationEngine.record_fill(state, order.order_id, ASSET, Decimal("4"), Decimal("404"))

    payload = deserialize(serialize(capture_allocation(state)))
    restored = restore_allocation(allocation_from_primitives(payload))

    assert payload["budget"]["currency"] == "EUR"
    assert restored == state, "PER-006: the currency was written and never read back"
    assert _capital(restored, "A").deployed == {ASSET: Decimal("404")}
    assert _capital(restored, "A").reserved == {order.order_id: Decimal("596")}


# --------------------------------------------------------------------------- #
# On the execution path
# --------------------------------------------------------------------------- #


class _Planned(BaseStrategy):
    def __init__(self, strategy_id: str, plan: Mapping[float, tuple[str, OrderTerms]]) -> None:
        self.strategy_id = strategy_id
        self.plan = dict(plan)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        planned = self.plan.get(event.quote.timestamp)
        if planned is None:
            return ()
        quantity, terms = planned
        return (
            Intent(
                self.strategy_id,
                ASSET,
                Decimal(quantity),
                terms=terms,
                timestamp=event.quote.timestamp,
            ),
        )


def _runtime(*strategies: _Planned) -> RuntimeState:
    runtime = create_runtime()
    for strategy in strategies:
        runtime = register_strategy(runtime, strategy.strategy_id, strategy)
    entries = {}
    for strategy_id, entry in runtime.strategies.items():
        entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
        entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
        entry, _ = RuntimeSupervisor.start(entry, 1.3)
        entries[strategy_id] = entry
    return replace(runtime, strategies=entries)


def _pipeline(*strategies: _Planned, budget: CapitalBudget) -> ExecutionPipelineState:
    config = replace(
        pipeline_config("A", simulator=ExecutionSimulator(cost_model=FREE)), budget=budget
    )
    return ExecutionPipeline.initialize(config, _runtime(*strategies), 1.0)


def _at(state: ExecutionPipelineState, timestamp: float, price: str = "100") -> Any:
    return ExecutionPipeline.process_quote(
        state, quote(ASSET, timestamp, Decimal(price)), context_factory
    )


MARKET = OrderTerms.market()


def test_positions_count_against_the_ceiling_and_selling_makes_room() -> None:
    strategy = _Planned(
        "A", {2.0: ("10", MARKET), 3.0: ("10", MARKET), 4.0: ("-5", MARKET), 5.0: ("10", MARKET)}
    )
    state = _pipeline(strategy, budget=_budget(A="1500"))

    state = _at(state, 2.0).state
    assert _capital(state.allocation, "A").deployed == {ASSET: Decimal("1000")}
    assert _capital(state.allocation, "A").reserved_total == ZERO, "converted by the fill"

    refused = _at(state, 3.0)
    assert refused.fills == ()
    assert "A has committed 1000 of its 1500 ceiling" in _refusals(refused.state.allocation)[-1]

    sold = _at(refused.state, 4.0, "120").state
    assert _capital(sold.allocation, "A").deployed == {ASSET: Decimal("500")}, "at cost"

    bought = _at(sold, 5.0)
    assert [fill.quantity for fill in bought.fills] == [Decimal("10")]
    assert _capital(bought.state.allocation, "A").committed == Decimal("1500")


def test_a_rising_price_does_not_lock_a_strategy_out() -> None:
    strategy = _Planned("A", {2.0: ("10", MARKET), 3.0: ("4", MARKET)})
    state = _at(_pipeline(strategy, budget=_budget(A="1500")), 2.0).state

    # Worth 1,200 at 120 but it cost 1,000: 4 more at 120 (480) fits under 1,500 at
    # cost, where at market (1,680) it would not.
    result = _at(state, 3.0, "120")

    assert [fill.quantity for fill in result.fills] == [Decimal("4")]
    assert _capital(result.state.allocation, "A").committed == Decimal("1480")


def test_a_working_order_reserves_until_its_life_ends() -> None:
    resting = OrderTerms.limit(Decimal("90"), TimeInForce.GTD, expire_at=4.0)
    strategy = _Planned("A", {2.0: ("10", resting), 3.0: ("8", MARKET)})
    state = _at(_pipeline(strategy, budget=_budget(A="1500")), 2.0).state
    assert _capital(state.allocation, "A").reserved_total == Decimal("1000")

    blocked = _at(state, 3.0)
    assert blocked.fills == (), "the working order's 1,000 counts"

    expired = _at(blocked.state, 4.0).state
    (order,) = expired.oms.orders.orders()
    assert order.status is OrderStatus.EXPIRED
    assert _capital(expired.allocation, "A").committed == ZERO


def test_without_enforcement_the_path_is_what_it_was() -> None:
    plain = CapitalBudget(
        Decimal("1000000"), Decimal("1000000"), strategy_budgets={"A": Decimal("1500")}
    )
    strategy = _Planned("A", {2.0: ("10", MARKET), 3.0: ("10", MARKET)})
    state = _at(_pipeline(strategy, budget=plain), 2.0).state
    result = _at(state, 3.0)

    assert [fill.quantity for fill in result.fills] == [Decimal("10")]
    assert result.state.allocation.strategy_capital == {}


def test_a_ceilinged_run_survives_a_pipeline_snapshot_and_keeps_enforcing() -> None:
    strategy = _Planned("A", {2.0: ("10", MARKET), 3.0: ("10", MARKET)})
    state = _at(_pipeline(strategy, budget=_budget(A="1500")), 2.0).state

    payload = deserialize(serialize(capture(state)))
    assert payload["config"]["budget"]["enforce_strategy_budgets"] is True
    restored = restore(
        from_primitives(payload),
        RuntimeObjects(
            sizing_model=state.config.sizing_model,
            simulator=state.config.simulator,
            strategies={"A": strategy},
        ),
    )

    assert restored == state
    assert _at(restored, 3.0).fills == ()
