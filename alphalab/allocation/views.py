"""Pure queries exposing transparent Allocation Engine access."""

from collections.abc import Sequence
from decimal import Decimal

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.request import OrderRequest
from alphalab.allocation.state import AllocationState


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
