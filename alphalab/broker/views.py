"""Pure queries exposing transparent Broker State access."""

from collections.abc import Sequence

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.position import BrokerPosition
from alphalab.broker.state import BrokerState


def account_snapshot(state: BrokerState) -> BrokerAccount:
    """Returns the most recent broker account financial snapshot."""
    return state.account


def open_orders(state: BrokerState) -> Sequence[BrokerOrder]:
    """Returns all currently active orders residing at the broker."""
    open_statuses = {
        BrokerOrderStatus.PENDING_SUBMIT,
        BrokerOrderStatus.ACCEPTED,
        BrokerOrderStatus.PARTIALLY_FILLED,
    }
    return tuple(o for o in state.orders.values() if o.status in open_statuses)


def positions(state: BrokerState) -> Sequence[BrokerPosition]:
    """Returns all open positions tracked by the broker."""
    return tuple(p for p in state.positions.values() if p.quantity != 0)


def executions(state: BrokerState) -> Sequence[BrokerExecution]:
    """Returns all executions processed by the broker."""
    return tuple(state.executions.values())