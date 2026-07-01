"""Immutable market tick model."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Tick:
    """Immutable representation of a single market trade (Tick)."""

    asset_id: str
    timestamp: float
    price: Decimal
    quantity: Decimal
    trade_id: str
    venue: str
    currency: str
