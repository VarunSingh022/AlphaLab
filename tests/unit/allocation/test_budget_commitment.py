"""What an order commits against the budget is the exposure it adds (ledger ALC-007).

Until v3.11 every order in a batch committed its whole notional, a sale as much
as a purchase. A fully invested book could therefore not rotate: selling one
holding and buying another in one batch committed twice the capital it used,
the budget refused the batch, and the sale was dropped with the purchase. The
risk gate has not refused a reduction since v3.10 (ledger KD-004); these tests
hold allocation to the same rule, and to sending reductions first so the risk
gate sees the exposure a sale frees.
"""

from __future__ import annotations

from decimal import Decimal

from alphalab.allocation import (
    AllocationConstraints,
    AllocationEngine,
    CapitalBudget,
    FixedQuantitySizing,
)
from alphalab.allocation.events import BudgetExceeded
from alphalab.allocation.state import AllocationState
from alphalab.core.enums import Side
from alphalab.core.order_request import OrderRequest
from alphalab.strategy.events import Intent

BUDGET = CapitalBudget(
    global_capital=Decimal("100000"),
    maximum_exposure=Decimal("100000"),
    cash_buffer=Decimal("0"),
    strategy_budgets={"S": Decimal("100000")},
)
PRICES = {"AAPL": Decimal("100"), "MSFT": Decimal("100")}
SHORTS = AllocationConstraints(allow_shorting=True, enforce_integer_quantities=False)
LONG_ONLY = AllocationConstraints(allow_shorting=False, enforce_integer_quantities=False)


def _allocate(
    intents: tuple[Intent, ...],
    positions: dict[str, Decimal] | None,
    constraints: AllocationConstraints = SHORTS,
) -> tuple[AllocationState, tuple[OrderRequest, ...]]:
    state = AllocationEngine.initialize(BUDGET)
    return AllocationEngine.allocate(
        state,
        intents,
        PRICES,
        FixedQuantitySizing(),
        constraints,
        1.0,
        positions=positions,
    )


def test_a_fully_invested_book_can_rotate_in_one_batch() -> None:
    intents = (Intent("S", "MSFT", Decimal("950")), Intent("S", "AAPL", Decimal("-950")))

    for constraints in (SHORTS, LONG_ONLY):
        state, orders = _allocate(intents, {"AAPL": Decimal("950")}, constraints)

        assert [(o.asset_id, o.side, o.quantity) for o in orders] == [
            ("AAPL", Side.SELL, Decimal("950")),
            ("MSFT", Side.BUY, Decimal("950")),
        ]
        # The purchase commits 95,000; the sale, which reduces, commits nothing.
        assert state.notional_allocated == Decimal("95000")
        assert sorted(state.reservations.values()) == [Decimal("0"), Decimal("95000")]
        assert not any(isinstance(event, BudgetExceeded) for event in state.events)


def test_positions_unknown_commit_every_order_in_full_as_before() -> None:
    intents = (Intent("S", "MSFT", Decimal("950")), Intent("S", "AAPL", Decimal("-950")))

    state, orders = _allocate(intents, None)

    # 190,000 against a 100,000 budget: the batch is refused, as it always was
    # when nothing says the sale reduces a position.
    assert orders == ()
    assert any(isinstance(event, BudgetExceeded) for event in state.events)


def test_a_sale_through_flat_commits_only_the_short_it_opens_beyond_the_long() -> None:
    # Long 100: selling 150 leaves a short of 50 -- gross exposure falls from
    # 100 to 50 units, so nothing is committed. Selling 300 leaves a short of
    # 200: 100 units more exposure than the long it replaces.
    state, _ = _allocate((Intent("S", "AAPL", Decimal("-150")),), {"AAPL": Decimal("100")})
    assert state.notional_allocated == Decimal("0")

    state, _ = _allocate((Intent("S", "AAPL", Decimal("-300")),), {"AAPL": Decimal("100")})
    assert state.notional_allocated == Decimal("10000")


def test_opening_and_adding_commit_their_whole_notional() -> None:
    state, _ = _allocate((Intent("S", "AAPL", Decimal("100")),), {})
    assert state.notional_allocated == Decimal("10000")

    state, _ = _allocate((Intent("S", "AAPL", Decimal("-100")),), {"AAPL": Decimal("-50")})
    assert state.notional_allocated == Decimal("10000")


def test_reductions_go_first_and_each_group_keeps_its_order() -> None:
    intents = (
        Intent("S", "MSFT", Decimal("10")),
        Intent("S", "AAPL", Decimal("-5")),
        Intent("S", "IBM", Decimal("7")),
        Intent("S", "ORCL", Decimal("-3")),
    )
    prices = {**PRICES, "IBM": Decimal("100"), "ORCL": Decimal("100")}
    state = AllocationEngine.initialize(BUDGET)
    _, orders = AllocationEngine.allocate(
        state,
        intents,
        prices,
        FixedQuantitySizing(),
        SHORTS,
        1.0,
        positions={"AAPL": Decimal("5"), "ORCL": Decimal("3")},
    )

    assert [o.asset_id for o in orders] == ["AAPL", "ORCL", "MSFT", "IBM"]


def test_a_batch_of_one_kind_is_sent_in_the_order_it_was_asked_for() -> None:
    intents = (Intent("S", "MSFT", Decimal("10")), Intent("S", "AAPL", Decimal("10")))

    _, orders = _allocate(intents, {})

    assert [o.asset_id for o in orders] == ["MSFT", "AAPL"]
