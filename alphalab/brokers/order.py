"""Immutable models defining orders routed to brokers."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto


class OrderType(Enum):
    """Standard execution types."""

    MARKET = auto()
    LIMIT = auto()
    STOP = auto()
    STOP_LIMIT = auto()


class TimeInForce(Enum):
    """Order duration modifiers."""

    IOC = auto()
    FOK = auto()
    DAY = auto()
    GTC = auto()


class OrderSide(Enum):
    """Execution side."""

    BUY = auto()
    SELL = auto()


class OrderStatus(Enum):
    """Lifecycle stages of an active order."""

    PENDING = auto()
    SUBMITTED = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    CANCELLED = auto()
    REJECTED = auto()
    EXPIRED = auto()


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """Immutable representation of a trade request routed to a broker."""

    order_id: str
    account_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    tif: TimeInForce
    quantity: Decimal
    price: Decimal
    stop_price: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal
    status: OrderStatus
    created_at: float
    updated_at: float
