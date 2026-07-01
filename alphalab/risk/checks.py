"""Pure stateless risk check functions returning violations if breached."""

from decimal import Decimal

from alphalab.risk.models import OrderRequest, OrderSide, RiskViolation
from alphalab.risk.state import RiskState


def check_order_size(request: OrderRequest, state: RiskState) -> RiskViolation | None:
    limit = state.active_limits.order_size
    if request.quantity > limit.max_quantity:
        return RiskViolation(
            rule="OrderSizeQuantity",
            description="Order quantity exceeds max allowed.",
            severity="HIGH",
            current_value=request.quantity,
            allowed_value=limit.max_quantity,
        )
    if request.notional_value > limit.max_notional:
        return RiskViolation(
            rule="OrderSizeNotional",
            description="Order notional value exceeds max allowed.",
            severity="HIGH",
            current_value=request.notional_value,
            allowed_value=limit.max_notional,
        )
    return None


def check_position_limit(request: OrderRequest, state: RiskState) -> RiskViolation | None:
    limit = state.active_limits.position
    current_pos = state.exposure.asset_exposure.get(request.asset_id, Decimal("0.00"))

    # Calculate post-trade position assuming full fill
    trade_qty = request.quantity if request.side == OrderSide.BUY else -request.quantity
    projected_pos = abs(current_pos + trade_qty)

    if projected_pos > limit.max_quantity:
        return RiskViolation(
            rule="PositionLimit",
            description="Projected position exceeds maximum allowed.",
            severity="HIGH",
            current_value=projected_pos,
            allowed_value=limit.max_quantity,
        )
    return None


def check_margin(request: OrderRequest, state: RiskState) -> RiskViolation | None:
    limit = state.active_limits.margin
    # Simplified margin impact estimation: full notional requires margin
    projected_margin = state.margin.margin_used + request.notional_value
    projected_util = Decimal("0.00")

    if state.margin.available_margin > Decimal("0.00"):
        projected_util = (projected_margin / state.margin.available_margin).quantize(
            Decimal("0.0001")
        )

    if projected_util > limit.max_margin_utilization:
        return RiskViolation(
            rule="MarginLimit",
            description="Projected margin utilization exceeds limit.",
            severity="CRITICAL",
            current_value=projected_util,
            allowed_value=limit.max_margin_utilization,
        )
    return None


def check_exposure(request: OrderRequest, state: RiskState) -> RiskViolation | None:
    limit = state.active_limits.exposure
    projected_gross = state.exposure.gross_exposure + request.notional_value

    if projected_gross > limit.max_gross_exposure:
        return RiskViolation(
            rule="GrossExposureLimit",
            description="Projected gross exposure exceeds limit.",
            severity="HIGH",
            current_value=projected_gross,
            allowed_value=limit.max_gross_exposure,
        )
    return None


def check_drawdown(state: RiskState) -> RiskViolation | None:
    limit = state.active_limits.drawdown
    if state.current_drawdown_pct > limit.max_drawdown_pct:
        return RiskViolation(
            rule="DrawdownLimit",
            description="Account has breached maximum drawdown limit.",
            severity="CRITICAL",
            current_value=state.current_drawdown_pct,
            allowed_value=limit.max_drawdown_pct,
        )
    return None


def check_buying_power(request: OrderRequest, state: RiskState) -> RiskViolation | None:
    if request.notional_value > state.buying_power:
        return RiskViolation(
            rule="BuyingPowerLimit",
            description="Insufficient buying power for order.",
            severity="HIGH",
            current_value=request.notional_value,
            allowed_value=state.buying_power,
        )
    return None


def check_daily_loss(state: RiskState) -> RiskViolation | None:
    limit = state.active_limits.daily_loss
    if state.daily_loss > limit.max_daily_loss:
        return RiskViolation(
            rule="DailyLossLimit",
            description="Account has breached maximum daily loss.",
            severity="CRITICAL",
            current_value=state.daily_loss,
            allowed_value=limit.max_daily_loss,
        )
    return None


def check_leverage(request: OrderRequest, state: RiskState) -> RiskViolation | None:
    limit = state.active_limits.leverage
    if state.current_nav <= Decimal("0.00"):
        return None

    projected_gross = state.exposure.gross_exposure + request.notional_value
    projected_leverage = (projected_gross / state.current_nav).quantize(Decimal("0.0001"))

    if projected_leverage > limit.max_leverage:
        return RiskViolation(
            rule="LeverageLimit",
            description="Projected leverage exceeds maximum allowed.",
            severity="HIGH",
            current_value=projected_leverage,
            allowed_value=limit.max_leverage,
        )
    return None
