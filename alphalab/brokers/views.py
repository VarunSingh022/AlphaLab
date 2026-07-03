"""Pure queries exposing transparent Broker Connector State access."""

from collections.abc import Sequence

from alphalab.brokers.account import AccountSnapshot
from alphalab.brokers.connection import BrokerConnection
from alphalab.brokers.execution import ExecutionReport
from alphalab.brokers.order import BrokerOrder, OrderStatus
from alphalab.brokers.position import PositionSnapshot
from alphalab.brokers.state import BrokerConnectorState, BrokerStatistics


def active_brokers(state: BrokerConnectorState) -> Sequence[BrokerConnection]:
    """Returns all currently registered broker connections."""
    return tuple(state.connections.values())


def get_account(state: BrokerConnectorState, account_id: str) -> AccountSnapshot | None:
    """Returns the financial snapshot of a specific account."""
    return state.accounts.get(account_id)


def list_positions(state: BrokerConnectorState, account_id: str) -> Sequence[PositionSnapshot]:
    """Returns all open positions mapping to a specific account."""
    return tuple(pos for pos in state.positions.values() if pos.account_id == account_id)


def open_orders(state: BrokerConnectorState) -> Sequence[BrokerOrder]:
    """Returns all non-terminal active orders across the framework."""
    active_statuses = {OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}
    return tuple(o for o in state.orders.values() if o.status in active_statuses)


def list_executions(state: BrokerConnectorState, order_id: str) -> Sequence[ExecutionReport]:
    """Returns all execution fills applied to a specific order."""
    return tuple(e for e in state.executions.values() if e.order_id == order_id)


def engine_statistics(state: BrokerConnectorState) -> BrokerStatistics:
    """Returns global routing and settlement metrics."""
    return state.statistics
