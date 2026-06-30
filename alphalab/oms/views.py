"""Pure queries to expose transparent OMS access."""

from collections.abc import Sequence
from decimal import Decimal

from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order
from alphalab.oms.state import OMSState


def order(state: OMSState, order_id: OrderId) -> Order:
    return state.orders.find(order_id)


def orders(state: OMSState) -> Sequence[Order]:
    return state.orders.orders()


def active_orders(state: OMSState) -> Sequence[Order]:
    return tuple(state.orders.find(oid) for oid in state.active_orders)


def completed_orders(state: OMSState) -> Sequence[Order]:
    return tuple(state.orders.find(oid) for oid in state.completed_orders)


def orders_for_asset(state: OMSState, asset_id: str) -> Sequence[Order]:
    return state.orders.orders_for_asset(asset_id)


def orders_for_strategy(state: OMSState, strategy_id: str) -> Sequence[Order]:
    return state.orders.orders_for_strategy(strategy_id)


def filled_volume(state: OMSState, asset_id: str) -> Decimal:
    """Computes aggregate filled volume for an asset."""
    target_orders = state.orders.orders_for_asset(asset_id)
    return sum((o.filled_quantity for o in target_orders), Decimal("0"))
