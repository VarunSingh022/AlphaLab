"""Discrete trading actions and their translation into an order quantity.

AlphaLab's default allocation sizing model (`alphalab.allocation.sizing.FixedQuantitySizing`)
treats an `Intent.target` directly as the signed quantity to trade -- positive to
buy, negative to sell -- not a target position reconciled against current holdings.
Verified by reading `alphalab.allocation.engine.AllocationEngine.allocate`'s netting
step directly rather than assuming from `Intent`'s more general docstring, since
building this environment on the wrong interpretation would have made every trade
wrong in a way that might not be obvious from the tests alone.
"""

from decimal import Decimal
from enum import Enum, auto

from alphalab.reinforcement_learning.exceptions import RLInputError


class Action(Enum):
    """The three actions a trading agent may take at each step."""

    HOLD = auto()
    BUY = auto()
    SELL = auto()


def action_to_signed_quantity(action: Action, trade_size: Decimal) -> Decimal:
    """Converts a discrete action into a signed order quantity.

    HOLD returns exactly zero, which the allocation engine's netting step drops
    entirely (a net quantity of zero generates no order at all) -- so HOLD
    genuinely produces no trade, not a zero-sized no-op order.

    Raises:
        RLInputError: If trade_size is not positive.
    """
    if trade_size <= Decimal("0"):
        raise RLInputError(f"trade_size must be positive, got {trade_size}.")

    if action is Action.HOLD:
        return Decimal("0")
    if action is Action.BUY:
        return trade_size
    return -trade_size
