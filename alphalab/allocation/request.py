"""Data transfer models crossing the Allocation boundary towards Risk and OMS."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto


class OrderSide(Enum):
    """Execution side for output Order Requests."""

    BUY = auto()
    SELL = auto()


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """Outgoing concrete order request sent to the Risk Engine."""

    order_id: str
    strategy_id: str
    asset_id: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    timestamp: float = 0.0
