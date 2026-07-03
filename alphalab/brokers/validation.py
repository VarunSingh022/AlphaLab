"""Strict validation rules for broker routing and state transitions."""

from decimal import Decimal

from alphalab.brokers.connection import BrokerConnection
from alphalab.brokers.exceptions import BrokerValidationError, InvalidBrokerStateError
from alphalab.brokers.order import BrokerOrder, OrderStatus
from alphalab.brokers.state import BrokerConnectorState


def validate_broker_registration(state: BrokerConnectorState, connection: BrokerConnection) -> None:
    if not connection.broker_id.strip():
        raise BrokerValidationError("Broker ID cannot be empty.")
    if connection.broker_id in state.connections:
        raise InvalidBrokerStateError(f"Broker '{connection.broker_id}' is already registered.")


def validate_account(state: BrokerConnectorState, account_id: str, broker_id: str) -> None:
    if account_id in state.accounts:
        raise InvalidBrokerStateError(f"Account '{account_id}' is already registered.")
    if broker_id not in state.connections:
        raise BrokerValidationError(f"Broker '{broker_id}' does not exist.")


def validate_order_submission(state: BrokerConnectorState, order: BrokerOrder) -> None:
    if order.account_id not in state.accounts:
        raise BrokerValidationError(f"Account '{order.account_id}' does not exist.")
    if order.order_id in state.orders:
        raise InvalidBrokerStateError(f"Order '{order.order_id}' is already tracked.")
    if order.quantity <= Decimal("0"):
        raise BrokerValidationError("Order quantity must be positive.")
    if order.price < Decimal("0") or order.stop_price < Decimal("0"):
        raise BrokerValidationError("Order price cannot be negative.")


def validate_order_cancellation(state: BrokerConnectorState, order_id: str) -> BrokerOrder:
    if order_id not in state.orders:
        raise BrokerValidationError(f"Order '{order_id}' not found.")

    order = state.orders[order_id]
    terminal_states = {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }
    if order.status in terminal_states:
        raise InvalidBrokerStateError(f"Cannot cancel order in terminal state {order.status.name}.")

    return order


def validate_execution(
    state: BrokerConnectorState, execution_id: str, order_id: str
) -> BrokerOrder:
    if execution_id in state.executions:
        raise InvalidBrokerStateError(f"Duplicate execution ID '{execution_id}'.")
    if order_id not in state.orders:
        raise BrokerValidationError(f"Execution references unknown order '{order_id}'.")

    return state.orders[order_id]
