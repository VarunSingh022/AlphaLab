"""Test suite for the Execution Engine."""

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.execution import (
    ExecutionEngine,
    ExecutionSimulator,
    ExecutionState,
    ExecutionValidationError,
    FillStatus,
    FixedSlippage,
    OrderInstruction,
    PercentageCommission,
    all_reports,
    reports_for_order,
)


@pytest.fixture
def base_instruction() -> OrderInstruction:
    return OrderInstruction(
        order_id="ORD-123",
        strategy_id="STRAT-1",
        asset_id="AAPL",
        quantity=Decimal("100"),
        price=Decimal("150.00"),
        side="BUY",
        venue="SIM",
        currency="USD",
    )


def test_full_fill_simulation(base_instruction: OrderInstruction) -> None:
    simulator = ExecutionSimulator()
    state = ExecutionState()

    new_state = ExecutionEngine.simulate(
        state=state,
        simulator=simulator,
        instruction=base_instruction,
        fill_quantity=Decimal("100"),
        market_price=Decimal("150.00"),
        timestamp=1000.0,
        status=FillStatus.FULL_FILL,
    )

    assert len(all_reports(new_state)) == 1
    rep = reports_for_order(new_state, "ORD-123")[0]
    assert rep.fill_quantity == Decimal("100")
    assert rep.fill_price == Decimal("150.00")
    assert rep.status == FillStatus.FULL_FILL


def test_partial_fill_simulation(base_instruction: OrderInstruction) -> None:
    simulator = ExecutionSimulator()
    state = ExecutionState()

    new_state = ExecutionEngine.simulate(
        state=state,
        simulator=simulator,
        instruction=base_instruction,
        fill_quantity=Decimal("40"),
        market_price=Decimal("150.00"),
        timestamp=1000.0,
        status=FillStatus.PARTIAL_FILL,
    )

    assert len(all_reports(new_state)) == 1
    rep = reports_for_order(new_state, "ORD-123")[0]
    assert rep.fill_quantity == Decimal("40")
    assert rep.status == FillStatus.PARTIAL_FILL


def test_slippage_model(base_instruction: OrderInstruction) -> None:
    simulator = ExecutionSimulator(slippage_model=FixedSlippage(Decimal("0.50")))

    # BUY should add slippage
    rep_buy = simulator.simulate_fill(
        base_instruction, Decimal("10"), Decimal("100.00"), 1000.0, FillStatus.FULL_FILL
    )
    assert rep_buy.fill_price == Decimal("100.50")

    # SELL should subtract slippage
    sell_inst = replace(base_instruction, side="SELL")
    rep_sell = simulator.simulate_fill(
        sell_inst, Decimal("10"), Decimal("100.00"), 1000.0, FillStatus.FULL_FILL
    )
    assert rep_sell.fill_price == Decimal("99.50")


def test_commission_model(base_instruction: OrderInstruction) -> None:
    simulator = ExecutionSimulator(commission_model=PercentageCommission(Decimal("0.01")))
    rep = simulator.simulate_fill(
        base_instruction, Decimal("10"), Decimal("100.00"), 1000.0, FillStatus.FULL_FILL
    )
    # 10 * 100 = 1000 value * 0.01 = 10.00
    assert rep.commission == Decimal("10.00")


def test_validation_failures(base_instruction: OrderInstruction) -> None:
    simulator = ExecutionSimulator()
    state = ExecutionState()

    with pytest.raises(ExecutionValidationError):
        ExecutionEngine.simulate(
            state=state,
            simulator=simulator,
            instruction=base_instruction,
            fill_quantity=Decimal("-10"),  # Negative quantity
            market_price=Decimal("150.00"),
            timestamp=1000.0,
            status=FillStatus.FULL_FILL,
        )


def test_immutability(base_instruction: OrderInstruction) -> None:
    state1 = ExecutionState()
    simulator = ExecutionSimulator()

    state2 = ExecutionEngine.simulate(
        state=state1,
        simulator=simulator,
        instruction=base_instruction,
        fill_quantity=Decimal("100"),
        market_price=Decimal("150.00"),
        timestamp=1000.0,
        status=FillStatus.FULL_FILL,
    )

    assert len(all_reports(state1)) == 0
    assert len(all_reports(state2)) == 1
    assert state1 is not state2
