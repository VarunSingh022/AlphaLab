"""Core data structures and request models for risk evaluation."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto


class OrderSide(Enum):
    """Execution side for risk evaluation."""

    BUY = auto()
    SELL = auto()


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """Immutable representation of a proposed order prior to OMS submission."""

    order_id: str
    strategy_id: str
    asset_id: str
    side: OrderSide
    quantity: Decimal
    price: Decimal

    @property
    def notional_value(self) -> Decimal:
        """Calculates the absolute notional value of the proposed order."""
        return (self.quantity * self.price).quantize(Decimal("0.0001"))


@dataclass(frozen=True, slots=True)
class RiskViolation:
    """Immutable record of a breached risk limit."""

    rule: str
    description: str
    severity: str
    current_value: Decimal
    allowed_value: Decimal
