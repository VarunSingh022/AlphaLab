"""Immutable models defining orders routed to brokers."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.core.enums import OrderStatus as CoreOrderStatus
from alphalab.core.enums import OrderType as CoreOrderType
from alphalab.core.enums import Side as CoreSide
from alphalab.core.enums import TimeInForce as CoreTimeInForce


class OrderStatus(Enum):
    """Connector-local operational states.

    `SUBMITTED` remains connector-local; all other lifecycle states map to
    `alphalab.core.enums.OrderStatus`.
    """

    SUBMITTED = auto()


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """Immutable representation of a trade request routed to a broker."""

    order_id: str
    account_id: str
    symbol: str
    side: CoreSide
    order_type: CoreOrderType
    tif: CoreTimeInForce
    quantity: Decimal
    price: Decimal
    stop_price: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal
    status: CoreOrderStatus | OrderStatus
    created_at: float
    updated_at: float
