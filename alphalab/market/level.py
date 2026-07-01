"""Immutable models representing order book price levels."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    """Immutable representation of a single price level in an order book."""

    price: Decimal
    size: Decimal
    orders: int
