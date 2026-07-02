"""Immutable broker order models."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto


class BrokerOrderStatus(Enum):
    """Lifecycle statuses for an order residing at the broker."""
    PENDING_SUBMIT = auto()
    ACCEPTED = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    PENDING_CANCEL = auto()
    CANCELLED = auto()
    REJECTED = auto()


class BrokerOrderType(Enum):
    """Standard broker order types."""
    MARKET = auto()
    LIMIT = auto()
    STOP = auto()


class BrokerOrderSide(Enum):
    """Execution side for broker orders."""
    BUY = auto()
    SELL = auto()


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """Immutable representation of an order residing at an external broker."""
    broker_order_id: str
    oms_order_id: str
    symbol: str
    side: BrokerOrderSide
    order_type: BrokerOrderType
    quantity: Decimal
    price: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal
    status: BrokerOrderStatus
    created_at: float
    updated_at: float