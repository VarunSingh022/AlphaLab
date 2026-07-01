"""Comprehensive unit tests for the Risk Engine."""

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.risk import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    ExposureStatus,
    LeverageLimit,
    MarginLimit,
    MarginStatus,
    OrderRequest,
    OrderSide,
    OrderSizeLimit,
    PositionLimit,
    RiskEngine,
    RiskLimits,
    RiskValidationError,
    latest_decision,
)


@pytest.fixture
def default_limits() -> RiskLimits:
    return RiskLimits(
        order_size=OrderSizeLimit(Decimal("1000"), Decimal("100000")),
        position=PositionLimit(Decimal("5000"), Decimal("500000")),
        exposure=ExposureLimit(Decimal("1000000"), Decimal("500000")),
        leverage=LeverageLimit(Decimal("2.0")),
        margin=MarginLimit(Decimal("0.80")),
        daily_loss=DailyLossLimit(Decimal("10000")),
        drawdown=DrawdownLimit(Decimal("0.10")),
    )


@pytest.fixture
def base_request() -> OrderRequest:
    return OrderRequest(
        order_id="ORD-001",
        strategy_id="STRAT-01",
        asset_id="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("150.00"),
    )


def test_order_validation(default_limits: RiskLimits) -> None:
    state = RiskEngine.reset(default_limits)
    bad_req = OrderRequest("O1", "S1", "AAPL", OrderSide.BUY, Decimal("-10"), Decimal("100"))

    with pytest.raises(RiskValidationError, match="Order quantity must be positive"):
        RiskEngine.evaluate(state, bad_req, 1000.0)


def test_clean_approval(default_limits: RiskLimits, base_request: OrderRequest) -> None:
    state = RiskEngine.reset(default_limits)
    # Give some buying power to pass BP checks
    state = RiskEngine.update_margin(
        state, MarginStatus(available_margin=Decimal("100000")), 1000.0
    )
    # Manually inject buying power (since RiskState is frozen, use internals for test setup)
    state = replace(
        state,
        buying_power=Decimal("50000"),
    )

    new_state, decision = RiskEngine.evaluate(state, base_request, 1000.0)

    assert decision.approved is True
    assert len(decision.violations) == 0
    assert latest_decision(new_state) == decision


def test_order_size_limit_rejection(default_limits: RiskLimits, base_request: OrderRequest) -> None:
    state = RiskEngine.reset(default_limits)
    state = replace(
        state,
        buying_power=Decimal("1000000"),
    )

    # 2000 quantity > 1000 limit
    huge_req = OrderRequest("O2", "S1", "AAPL", OrderSide.BUY, Decimal("2000"), Decimal("10.00"))

    _unused_state, decision = RiskEngine.evaluate(state, huge_req, 1000.0)

    assert decision.approved is False
    assert len(decision.violations) == 1
    assert decision.violations[0].rule == "OrderSizeQuantity"


def test_drawdown_limit_rejection(default_limits: RiskLimits, base_request: OrderRequest) -> None:
    state = RiskEngine.reset(default_limits)
    state = replace(
        state,
        buying_power=Decimal("50000"),
        peak_nav=Decimal("100000"),
        current_nav=Decimal("85000"),
    )
    _unused_state, decision = RiskEngine.evaluate(state, base_request, 1000.0)

    assert decision.approved is False
    assert any(v.rule == "DrawdownLimit" for v in decision.violations)


def test_buying_power_rejection(default_limits: RiskLimits, base_request: OrderRequest) -> None:
    state = RiskEngine.reset(default_limits)
    state = replace(
        state,
        buying_power=Decimal("100"),
    )

    # Order notional = 15000 > BP 100
    _unused_state, decision = RiskEngine.evaluate(state, base_request, 1000.0)

    assert decision.approved is False
    assert any(v.rule == "BuyingPowerLimit" for v in decision.violations)


def test_exposure_update_immutability(default_limits: RiskLimits) -> None:
    state1 = RiskEngine.reset(default_limits)
    exposure = ExposureStatus(gross_exposure=Decimal("50000"))

    state2 = RiskEngine.update_exposure(state1, exposure, 1000.0)

    assert state1.exposure.gross_exposure == Decimal("0.00")
    assert state2.exposure.gross_exposure == Decimal("50000")
    assert state1 is not state2


def test_margin_and_leverage_calcs(default_limits: RiskLimits) -> None:
    state = RiskEngine.reset(default_limits)
    state = replace(
        state,
        current_nav=Decimal("100000"),
    )

    exposure = ExposureStatus(gross_exposure=Decimal("150000"))
    state = RiskEngine.update_exposure(state, exposure, 1000.0)

    assert state.current_leverage == Decimal("1.5000")
