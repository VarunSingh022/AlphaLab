"""Validation rules for Order creation and mutation."""

from decimal import Decimal

from alphalab.oms.exceptions import OrderValidationError
from alphalab.oms.order import Order
from alphalab.oms.status import OrderType


def validate_order(order: Order, current_timestamp: float) -> None:
    """
    Validates structural and business invariants of an Order.
    Raises OrderValidationError if constraints are violated.
    """
    if order.quantity <= Decimal("0"):
        raise OrderValidationError("Order quantity must be strictly greater than 0.")

    if order.order_type == OrderType.LIMIT and order.limit_price is None:
        raise OrderValidationError("Limit orders require a valid limit price.")

    if order.order_type == OrderType.MARKET and order.limit_price is not None:
        raise OrderValidationError("Market orders must not have a limit price.")

    if order.order_type in {OrderType.STOP, OrderType.STOP_LIMIT} and order.stop_price is None:
        raise OrderValidationError("Stop orders require a valid stop price.")

    if order.remaining_quantity < Decimal("0"):
        raise OrderValidationError("Remaining quantity cannot be negative.")

    if order.filled_quantity > order.quantity:
        raise OrderValidationError("Filled quantity cannot exceed total quantity.")

    if order.average_fill_price < Decimal("0"):
        raise OrderValidationError("Average fill price cannot be negative.")

    if order.updated_at < order.created_at:
        raise OrderValidationError("Timestamps cannot move backwards (updated_at < created_at).")

    if order.updated_at < current_timestamp:
        raise OrderValidationError("Order timestamp cannot be earlier than system time.")
