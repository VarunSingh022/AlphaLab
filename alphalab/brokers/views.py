"""Pure queries exposing transparent Broker Connector State access."""

from collections.abc import Sequence

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.brokers.connection import BrokerConnection
from alphalab.brokers.order import OrderStatus
from alphalab.brokers.state import BrokerConnectorState, BrokerStatistics
from alphalab.core.enums import OrderStatus as CoreOrderStatus


def active_brokers(state: BrokerConnectorState) -> Sequence[BrokerConnection]:
    """Returns all currently registered broker connections."""
    return tuple(state.connections.values())


def get_account(state: BrokerConnectorState, account_id: str) -> BrokerAccount | None:
    """Returns the financial snapshot of a specific account."""
    return state.accounts.get(account_id)


def list_positions(state: BrokerConnectorState, account_id: str) -> Sequence[BrokerPosition]:
    """Returns all open positions mapping to a specific account."""
    return tuple(pos for pos in state.positions.values() if pos.account_id == account_id)


def open_orders(state: BrokerConnectorState) -> Sequence[BrokerOrder]:
    """Returns all non-terminal active orders across the framework."""
    active_statuses = {
        CoreOrderStatus.PENDING,
        OrderStatus.SUBMITTED,
        CoreOrderStatus.PARTIALLY_FILLED,
    }
    return tuple(o for o in state.orders.values() if o.status in active_statuses)


def list_executions(state: BrokerConnectorState, broker_order_id: str) -> Sequence[BrokerExecution]:
    """Returns all execution fills applied to a specific order."""
    return tuple(e for e in state.executions.values() if e.broker_order_id == broker_order_id)


def engine_statistics(state: BrokerConnectorState) -> BrokerStatistics:
    """Returns global routing and settlement metrics."""
    return state.statistics
