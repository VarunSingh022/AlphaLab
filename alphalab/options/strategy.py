"""Multi-leg option strategy payoff and P&L simulation.

`OptionLeg.side` reuses `alphalab.core.enums.Side` (BUY/SELL) rather than defining a
third independent buy/sell enum for options -- the same lesson from the domain model
unification in PR-034/035 applied going forward instead of retrofitted afterward.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.options.contract import OptionContract, occ_symbol
from alphalab.options.enums import OptionType
from alphalab.options.exceptions import OptionInputError


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """A single leg of a multi-leg option strategy.

    Attributes:
        contract: The contract this leg trades.
        side: BUY to go long the contract (pay premium), SELL to go short
            (receive premium).
        quantity: Number of contracts, always positive -- direction is carried by
            `side`, not the sign of `quantity`.
    """

    contract: OptionContract
    side: Side
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise OptionInputError(f"quantity must be positive, got {self.quantity}.")


@dataclass(frozen=True, slots=True)
class OptionStrategy:
    """An immutable collection of option legs traded as one unit."""

    legs: tuple[OptionLeg, ...]


def _leg_intrinsic_value(leg: OptionLeg, spot_price: Decimal) -> Decimal:
    """Per-share intrinsic value of a leg's contract at a given spot price."""
    if leg.contract.option_type is OptionType.CALL:
        return max(spot_price - leg.contract.strike, Decimal("0"))
    return max(leg.contract.strike - spot_price, Decimal("0"))


def compute_payoff_at_expiry(strategy: OptionStrategy, spot_price: Decimal) -> Decimal:
    """Computes total intrinsic-value payoff of a strategy at expiry.

    This is gross payoff, not P&L -- it does not subtract premiums paid or add
    premiums received. Use `compute_pnl` for net profit/loss.
    """
    total = Decimal("0")
    for leg in strategy.legs:
        intrinsic = _leg_intrinsic_value(leg, spot_price)
        sign = Decimal("1") if leg.side is Side.BUY else Decimal("-1")
        total += sign * intrinsic * leg.contract.multiplier * leg.quantity
    return total


def compute_pnl(
    strategy: OptionStrategy, entry_prices: Mapping[str, Decimal], spot_price: Decimal
) -> Decimal:
    """Computes net profit/loss of a strategy at a given spot price.

    Args:
        strategy: The strategy to evaluate.
        entry_prices: Per-share entry premium for each leg, keyed by
            `occ_symbol(leg.contract)`.
        spot_price: The underlying price to evaluate P&L at.

    Raises:
        OptionInputError: If entry_prices is missing an entry for any leg.
    """
    total = Decimal("0")
    for leg in strategy.legs:
        symbol = occ_symbol(leg.contract)
        if symbol not in entry_prices:
            raise OptionInputError(f"Missing entry price for leg '{symbol}'.")

        entry_price = entry_prices[symbol]
        intrinsic = _leg_intrinsic_value(leg, spot_price)
        sign = Decimal("1") if leg.side is Side.BUY else Decimal("-1")
        total += sign * (intrinsic - entry_price) * leg.contract.multiplier * leg.quantity
    return total
