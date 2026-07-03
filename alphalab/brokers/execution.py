"""Immutable models defining execution reports from brokers."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Immutable record of a partial or complete order fill."""

    execution_id: str
    order_id: str
    account_id: str
    symbol: str
    fill_quantity: Decimal
    fill_price: Decimal
    commission: Decimal
    timestamp: float
