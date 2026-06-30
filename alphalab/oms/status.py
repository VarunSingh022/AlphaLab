"""Enumerations representing classification and states of Orders."""

from enum import Enum, auto


class OrderStatus(Enum):
    """Defines the lifecycle stages of an order."""

    NEW = auto()
    PENDING = auto()
    ACCEPTED = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    CANCEL_PENDING = auto()
    CANCELLED = auto()
    REJECTED = auto()
    EXPIRED = auto()


class Side(Enum):
    """Defines the market side of the order."""

    BUY = auto()
    SELL = auto()


class OrderType(Enum):
    """Defines the execution methodology."""

    MARKET = auto()
    LIMIT = auto()
    STOP = auto()
    STOP_LIMIT = auto()
