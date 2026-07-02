"""Comprehensive unit tests ensuring deterministic sizing, netting, and budgeting."""

import math
from decimal import Decimal

import pytest

from alphalab.allocation import (
    AllocationConstraints,
    AllocationEngine,
    AllocationValidationError,
    CapitalBudget,
    EqualWeightSizing,
    FixedDollarSizing,
    FixedQuantitySizing,
    OrderSide,
    TargetWeightSizing,
    VolatilityTargetSizing,
    allocation_history,
    total_notional_allocated,
    validate_intent,
)
from alphalab.strategy.events import Intent


@pytest.fixture
def default_budget() -> CapitalBudget:
    return CapitalBudget(
        global_capital=Decimal("1000000.00"),
        maximum_exposure=Decimal("2000000.00"),
        cash_buffer=Decimal("50000.00"),
        strategy_budgets={"STRAT-A": Decimal("500000.00"), "STRAT-B": Decimal("500000.00")},
    )


@pytest.fixture
def default_constraints() -> AllocationConstraints:
    return AllocationConstraints(
        allow_shorting=True,
        enforce_integer_quantities=False,
    )


def test_validation_rejects_nan() -> None:
    intent = Intent("S1", "AAPL", Decimal(math.nan))
    with pytest.raises(AllocationValidationError):
        validate_intent(intent)


def test_validation_rejects_negative_timestamp() -> None:
    intent = Intent("S1", "AAPL", Decimal("100"), timestamp=-1.0)
    with pytest.raises(AllocationValidationError):
        validate_intent(intent)


def test_sizing_fixed_quantity(default_budget: CapitalBudget) -> None:
    sizer = FixedQuantitySizing()
    intent = Intent("S1", "AAPL", Decimal("150"))
    qty = sizer.calculate(intent, default_budget, Decimal("100.00"))
    assert qty == Decimal("150.000000")


def test_sizing_fixed_dollar(default_budget: CapitalBudget) -> None:
    sizer = FixedDollarSizing()
    intent = Intent("S1", "AAPL", Decimal("15000"))
    qty = sizer.calculate(intent, default_budget, Decimal("100.00"))
    assert qty == Decimal("150.000000")


def test_sizing_target_weight(default_budget: CapitalBudget) -> None:
    sizer = TargetWeightSizing()
    # 10% weight of STRAT-A's 500,000 budget = 50,000 dollars / 100 price = 500 shares
    intent = Intent("STRAT-A", "AAPL", Decimal("0.10"))
    qty = sizer.calculate(intent, default_budget, Decimal("100.00"))
    assert qty == Decimal("500.000000")


def test_sizing_equal_weight(default_budget: CapitalBudget) -> None:
    sizer = EqualWeightSizing(num_assets=5)
    # Target value doesn't matter for magnitude, only sign
    intent = Intent("STRAT-B", "AAPL", Decimal("1.0"))
    # 500,000 budget / 5 assets = 100,000 dollars / 100 price = 1000 shares
    qty = sizer.calculate(intent, default_budget, Decimal("100.00"))
    assert qty == Decimal("1000.000000")


def test_sizing_vol_target(default_budget: CapitalBudget) -> None:
    asset_vols = {"AAPL": Decimal("0.20")}
    sizer = VolatilityTargetSizing(target_vol=Decimal("0.10"), asset_vols=asset_vols)

    intent = Intent("STRAT-A", "AAPL", Decimal("1.0"))
    # Vol scaler: 0.10 / 0.20 = 0.50
    # Exposure: 0.50 * 500,000 = 250,000 dollars
    # Quantity: 250,000 / 100 = 2500 shares
    qty = sizer.calculate(intent, default_budget, Decimal("100.00"))
    assert qty == Decimal("2500.000000")


def test_engine_netting_offsets(
    default_budget: CapitalBudget, default_constraints: AllocationConstraints
) -> None:
    state = AllocationEngine.initialize(default_budget)
    sizer = FixedQuantitySizing()

    # Strat A buys 100, Strat B sells 70 -> Net Buy 30
    intents = (
        Intent("STRAT-A", "AAPL", Decimal("100")),
        Intent("STRAT-B", "AAPL", Decimal("-70")),
    )
    prices = {"AAPL": Decimal("150.00")}

    new_state, orders = AllocationEngine.allocate(
        state, intents, prices, sizer, default_constraints, 1000.0
    )

    assert len(orders) == 1
    assert orders[0].asset_id == "AAPL"
    assert orders[0].side == OrderSide.BUY
    assert orders[0].quantity == Decimal("30")

    # Total Notional allocated = 30 * 150 = 4500
    assert total_notional_allocated(new_state) == Decimal("4500.00")


def test_engine_budget_breach(
    default_budget: CapitalBudget, default_constraints: AllocationConstraints
) -> None:
    state = AllocationEngine.initialize(default_budget)
    sizer = FixedQuantitySizing()

    # 10,000 shares * 150 = 1,500,000 > Global Capital (950,000 after buffer)
    intents = (Intent("STRAT-A", "AAPL", Decimal("10000")),)
    prices = {"AAPL": Decimal("150.00")}

    new_state, orders = AllocationEngine.allocate(
        state, intents, prices, sizer, default_constraints, 1000.0
    )

    # Budget breached, batch dropped, 0 orders
    assert len(orders) == 0
    assert total_notional_allocated(new_state) == Decimal("0.00")

    # Ensure budget exceeded event was logged
    assert any(type(e).__name__ == "BudgetExceeded" for e in new_state.events)


def test_engine_shorting_constraint(
    default_budget: CapitalBudget,
) -> None:
    state = AllocationEngine.initialize(default_budget)
    sizer = FixedQuantitySizing()

    constraints = AllocationConstraints(allow_shorting=False)

    # Sell intent should be rejected due to constraints
    intents = (Intent("STRAT-A", "AAPL", Decimal("-100")),)
    prices = {"AAPL": Decimal("150.00")}

    new_state, orders = AllocationEngine.allocate(
        state, intents, prices, sizer, constraints, 1000.0
    )

    assert len(orders) == 0
    assert any(type(e).__name__ == "AllocationRejected" for e in new_state.events)


def test_engine_integer_quantities(
    default_budget: CapitalBudget,
) -> None:
    state = AllocationEngine.initialize(default_budget)
    sizer = FixedQuantitySizing()

    constraints = AllocationConstraints(enforce_integer_quantities=True)

    intents = (Intent("STRAT-A", "AAPL", Decimal("10.75")),)
    prices = {"AAPL": Decimal("150.00")}

    _, orders = AllocationEngine.allocate(state, intents, prices, sizer, constraints, 1000.0)

    assert len(orders) == 1
    # 10.75 should truncate/round to 11
    assert orders[0].quantity == Decimal("11")


def test_immutability(
    default_budget: CapitalBudget, default_constraints: AllocationConstraints
) -> None:
    state1 = AllocationEngine.initialize(default_budget)
    sizer = FixedQuantitySizing()

    intents = (Intent("S1", "AAPL", Decimal("100")),)
    prices = {"AAPL": Decimal("100.00")}

    state2, _ = AllocationEngine.allocate(
        state1, intents, prices, sizer, default_constraints, 1000.0
    )

    assert len(allocation_history(state1)) == 0
    assert len(allocation_history(state2)) == 1
    assert state1 is not state2
