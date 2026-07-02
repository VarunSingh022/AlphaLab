"""Immutable execution models."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BrokerExecution:
    """Immutable report of a single order fill from the broker."""
    execution_id: str
    broker_order_id: str
    symbol: str
    fill_quantity: Decimal
    fill_price: Decimal
    commission: Decimal
    timestamp: float