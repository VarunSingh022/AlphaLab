"""Validation rules ensuring structural integrity of broker communications."""

from decimal import Decimal

from alphalab.broker.exceptions import BrokerValidationError, InvalidBrokerStateError
from alphalab.broker.order import BrokerOrder
from alphalab.broker.state import BrokerState


def validate_order_submission(state: BrokerState, order: BrokerOrder) -> None:
    """Validates structural and logical integrity of an outbound order."""
    if order.quantity <= Decimal("0.00"):
        raise BrokerValidationError(f"Order quantity must be positive, got {order.quantity}")

    # The sign of a price is the instrument's question since v3.11 (ACC-007).
    if not order.price.is_finite():
        raise BrokerValidationError(f"Order price must be a finite number, got {order.price}")

    if order.broker_order_id in state.orders:
        raise BrokerValidationError(f"Duplicate broker_order_id: {order.broker_order_id}")


def validate_cancel_request(state: BrokerState, broker_order_id: str) -> None:
    """Validates whether an order can be cancelled.

    Every terminal status refuses, :attr:`~alphalab.broker.order.BrokerOrder.is_terminal`
    decides which those are. Until v3.9 this checked its own list of three and
    left out ``EXPIRED``, so an expired order could be cancelled -- one terminal
    status overwritten by another, the move the canonical lifecycle
    (:data:`~alphalab.core.lifecycle.ORDER_TRANSITIONS`) has no row for.
    """
    if broker_order_id not in state.orders:
        raise BrokerValidationError(f"Order {broker_order_id} not found.")

    order = state.orders[broker_order_id]

    if order.is_terminal:
        raise InvalidBrokerStateError(
            f"Cannot cancel order {broker_order_id} in status {order.status.name}"
        )


def validate_replace_request(
    state: BrokerState, broker_order_id: str, new_quantity: Decimal, new_price: Decimal
) -> None:
    """Validates whether an order can be amended to ``new_quantity`` at ``new_price``.

    The cancel rules, plus the arithmetic an amendment adds: the new quantity
    must exceed what has already filled -- an amendment that leaves nothing
    working is a cancel, and one below the filled quantity describes an order
    that executed more than it was for -- and the price must not be negative.
    """
    validate_cancel_request(state, broker_order_id)

    order = state.orders[broker_order_id]
    if new_quantity <= order.filled_quantity:
        raise BrokerValidationError(
            f"Cannot amend order {broker_order_id} to {new_quantity}: "
            f"{order.filled_quantity} has already filled, so nothing would be left working."
        )
    if not new_price.is_finite():
        raise BrokerValidationError(f"Order price must be a finite number, got {new_price}")


def validate_execution(state: BrokerState, execution_id: str) -> None:
    """Ensures execution integrity."""
    if execution_id in state.executions:
        raise BrokerValidationError(f"Duplicate execution_id: {execution_id}")
