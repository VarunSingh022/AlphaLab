"""Validation rules ensuring structural integrity of broker communications."""

from decimal import Decimal

from alphalab.broker.exceptions import BrokerValidationError, InvalidBrokerStateError
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.state import BrokerState


def validate_order_submission(state: BrokerState, order: BrokerOrder) -> None:
    """Validates structural and logical integrity of an outbound order."""
    if order.quantity <= Decimal("0.00"):
        raise BrokerValidationError(f"Order quantity must be positive, got {order.quantity}")
        
    if order.price < Decimal("0.00"):
        raise BrokerValidationError(f"Order price cannot be negative, got {order.price}")

    if order.broker_order_id in state.orders:
        raise BrokerValidationError(f"Duplicate broker_order_id: {order.broker_order_id}")


def validate_cancel_request(state: BrokerState, broker_order_id: str) -> None:
    """Validates whether an order can be cancelled."""
    if broker_order_id not in state.orders:
        raise BrokerValidationError(f"Order {broker_order_id} not found.")
        
    order = state.orders[broker_order_id]
    
    if order.status in {
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCELLED,
        BrokerOrderStatus.REJECTED,
    }:
        raise InvalidBrokerStateError(
            f"Cannot cancel order {broker_order_id} in status {order.status.name}"
        )


def validate_execution(state: BrokerState, execution_id: str) -> None:
    """Ensures execution integrity."""
    if execution_id in state.executions:
        raise BrokerValidationError(f"Duplicate execution_id: {execution_id}")