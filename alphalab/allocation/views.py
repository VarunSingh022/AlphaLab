"""Pure queries exposing transparent Allocation Engine access."""

from collections.abc import Mapping, Sequence
from decimal import Decimal

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.state import AllocationState
from alphalab.core.order_request import OrderRequest


def current_budget(state: AllocationState) -> CapitalBudget:
    """Returns the current capital budget rules."""
    return state.budget


def allocation_history(state: AllocationState) -> Sequence[OrderRequest]:
    """Returns all successfully netted and budgeted OrderRequests."""
    return state.history


def total_notional_allocated(state: AllocationState) -> Decimal:
    """Returns the total absolute notional value allocated historically."""
    return state.notional_allocated


def recent_orders_for_asset(state: AllocationState, asset_id: str) -> Sequence[OrderRequest]:
    """Returns generated OrderRequests filtered by asset ID."""
    return tuple(order for order in state.history if order.asset_id == asset_id)


def reserved_for_order(state: AllocationState, order_id: str) -> Decimal:
    """Returns the capital still reserved against a single order request."""
    return state.reservations.get(order_id, Decimal("0.00"))


def open_reservations(state: AllocationState) -> Mapping[str, Decimal]:
    """Returns every live reservation, keyed by order request id."""
    return state.reservations.to_dict()
