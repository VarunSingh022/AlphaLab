"""Strict validation rules for broker routing and state transitions."""

from decimal import Decimal

from alphalab.brokers.connection import BrokerConnection
from alphalab.brokers.exceptions import BrokerValidationError, InvalidBrokerStateError
from alphalab.brokers.order import BrokerOrder
from alphalab.brokers.state import BrokerConnectorState
from alphalab.common.validators import (
    require_mapping_key,
    require_missing_mapping_key,
    require_non_empty_string,
)
from alphalab.core.enums import OrderStatus as CoreOrderStatus


def validate_broker_registration(state: BrokerConnectorState, connection: BrokerConnection) -> None:
    require_non_empty_string(
        connection.broker_id,
        "broker_id",
        message="Broker ID cannot be empty.",
        exception_type=BrokerValidationError,
    )
    require_missing_mapping_key(
        state.connections,
        connection.broker_id,
        f"Broker '{connection.broker_id}' is already registered.",
        exception_type=InvalidBrokerStateError,
    )


def validate_account(state: BrokerConnectorState, account_id: str, broker_id: str) -> None:
    require_missing_mapping_key(
        state.accounts,
        account_id,
        f"Account '{account_id}' is already registered.",
        exception_type=InvalidBrokerStateError,
    )
    require_mapping_key(
        state.connections,
        broker_id,
        f"Broker '{broker_id}' does not exist.",
        exception_type=BrokerValidationError,
    )


def validate_order_submission(state: BrokerConnectorState, order: BrokerOrder) -> None:
    require_mapping_key(
        state.accounts,
        order.account_id,
        f"Account '{order.account_id}' does not exist.",
        exception_type=BrokerValidationError,
    )
    require_missing_mapping_key(
        state.orders,
        order.order_id,
        f"Order '{order.order_id}' is already tracked.",
        exception_type=InvalidBrokerStateError,
    )
    if order.quantity <= Decimal("0"):
        raise BrokerValidationError("Order quantity must be positive.")
    if order.price < Decimal("0") or order.stop_price < Decimal("0"):
        raise BrokerValidationError("Order price cannot be negative.")


def validate_order_cancellation(state: BrokerConnectorState, order_id: str) -> BrokerOrder:
    require_mapping_key(
        state.orders,
        order_id,
        f"Order '{order_id}' not found.",
        exception_type=BrokerValidationError,
    )

    order = state.orders[order_id]
    terminal_states = {
        CoreOrderStatus.FILLED,
        CoreOrderStatus.CANCELLED,
        CoreOrderStatus.REJECTED,
        CoreOrderStatus.EXPIRED,
    }
    if order.status in terminal_states:
        raise InvalidBrokerStateError(f"Cannot cancel order in terminal state {order.status.name}.")

    return order


def validate_execution(
    state: BrokerConnectorState, execution_id: str, order_id: str
) -> BrokerOrder:
    require_missing_mapping_key(
        state.executions,
        execution_id,
        f"Duplicate execution ID '{execution_id}'.",
        exception_type=InvalidBrokerStateError,
    )
    require_mapping_key(
        state.orders,
        order_id,
        f"Execution references unknown order '{order_id}'.",
        exception_type=BrokerValidationError,
    )

    return state.orders[order_id]
