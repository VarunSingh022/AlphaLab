"""Immutable broker order models.

This module retains broker-local operational statuses (staging/cancel-in-flight)
but routes all shared lifecycle/state and type values to the canonical core
enumerations in `alphalab.core.enums`.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.core.enums import OrderStatus as CoreOrderStatus
from alphalab.core.enums import OrderType as CoreOrderType
from alphalab.core.enums import Side as CoreSide


class BrokerOrderStatus(Enum):
    """Broker-local operational states that must remain subsystem-specific.

    Only non-canonical, connector/broker workflow values live here. Shared
    lifecycle statuses (accepted, filled, etc.) are represented by
    `alphalab.core.enums.OrderStatus`.
    """

    PENDING_SUBMIT = auto()
    PENDING_CANCEL = auto()


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """Immutable representation of an order residing at an external broker.

    - `side` and `order_type` use canonical core enums.
    - `status` may be either a core `OrderStatus` (shared lifecycle) or a
      broker-local `BrokerOrderStatus` (operational lifecycle).
    """

    broker_order_id: str
    oms_order_id: str
    symbol: str
    side: CoreSide
    order_type: CoreOrderType
    quantity: Decimal
    price: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal
    status: CoreOrderStatus | BrokerOrderStatus
    created_at: float
    updated_at: float
