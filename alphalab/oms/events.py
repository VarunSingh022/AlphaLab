"""Immutable domain events describing changes in OMS State."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order


@dataclass(frozen=True, slots=True)
class OMSEvent:
    """Base class for all Order Management System events."""

    event_id: str
    timestamp: float
    order_id: OrderId


@dataclass(frozen=True, slots=True)
class OrderSubmitted(OMSEvent):
    order: Order


@dataclass(frozen=True, slots=True)
class OrderAccepted(OMSEvent):
    pass


@dataclass(frozen=True, slots=True)
class OrderRejected(OMSEvent):
    reason: str


@dataclass(frozen=True, slots=True)
class OrderCancelled(OMSEvent):
    pass


@dataclass(frozen=True, slots=True)
class OrderExpired(OMSEvent):
    pass


@dataclass(frozen=True, slots=True)
class OrderReplaced(OMSEvent):
    new_quantity: Decimal
    new_limit_price: Decimal | None


@dataclass(frozen=True, slots=True)
class OrderPartiallyFilled(OMSEvent):
    fill_quantity: Decimal
    fill_price: Decimal


@dataclass(frozen=True, slots=True)
class OrderFilled(OMSEvent):
    fill_quantity: Decimal
    fill_price: Decimal
