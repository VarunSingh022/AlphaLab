"""Execution report definitions."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.execution.fill import FillStatus


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Deterministic, immutable execution report consumed by the Portfolio Engine."""

    execution_id: str
    order_id: str
    asset_id: str
    strategy_id: str
    timestamp: float
    fill_price: Decimal
    fill_quantity: Decimal
    commission: Decimal
    slippage: Decimal
    liquidity_flag: str
    venue: str
    currency: str
    status: FillStatus
