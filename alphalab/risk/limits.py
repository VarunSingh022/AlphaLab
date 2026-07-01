"""Immutable risk limit definitions."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PositionLimit:
    max_quantity: Decimal
    max_notional: Decimal


@dataclass(frozen=True, slots=True)
class OrderSizeLimit:
    max_quantity: Decimal
    max_notional: Decimal


@dataclass(frozen=True, slots=True)
class ExposureLimit:
    max_gross_exposure: Decimal
    max_net_exposure: Decimal


@dataclass(frozen=True, slots=True)
class LeverageLimit:
    max_leverage: Decimal


@dataclass(frozen=True, slots=True)
class MarginLimit:
    max_margin_utilization: Decimal


@dataclass(frozen=True, slots=True)
class DailyLossLimit:
    max_daily_loss: Decimal


@dataclass(frozen=True, slots=True)
class DrawdownLimit:
    max_drawdown_pct: Decimal


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Aggregated risk limits configuration for an account or strategy."""

    order_size: OrderSizeLimit
    position: PositionLimit
    exposure: ExposureLimit
    leverage: LeverageLimit
    margin: MarginLimit
    daily_loss: DailyLossLimit
    drawdown: DrawdownLimit
