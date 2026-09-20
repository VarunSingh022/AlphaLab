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
from alphalab.options.greeks import Greeks


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


def signed_quantity(leg: OptionLeg) -> Decimal:
    """The leg's contract count, signed the way a ``Position`` would be.

    ``OptionLeg`` carries direction in :attr:`~OptionLeg.side` and magnitude in
    a positive :attr:`~OptionLeg.quantity`, which is the right shape for an
    order and the wrong one for arithmetic. Every function that has to combine
    legs converts here rather than spelling the same conditional again --
    ``tests/regression/test_v34_invariants.py`` reads the source to keep a
    second spelling from appearing, because a sign convention applied twice in
    two places is a sign convention that will eventually disagree with itself.
    """

    magnitude = Decimal(leg.quantity)
    return magnitude if leg.side is Side.BUY else -magnitude


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
        total += intrinsic * leg.contract.multiplier * signed_quantity(leg)
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
        total += (intrinsic - entry_price) * leg.contract.multiplier * signed_quantity(leg)
    return total


def net_premium(strategy: OptionStrategy, entry_prices: Mapping[str, Decimal]) -> Decimal:
    """Cash paid or received to put the strategy on, signed.

    Negative means premium was paid out -- a long call costs money -- and
    positive means it was received. That is the cash-flow convention, the
    opposite of "the strategy's cost", and it is stated because both are in use
    and they differ only by a sign that nothing downstream would catch.

    Each leg contributes ``-price * multiplier * signed_quantity``, so a leg's
    own multiplier applies to that leg. A strategy mixing contracts with
    different multipliers -- a ratio spread across two listings -- is summed
    correctly rather than being scaled by whichever multiplier was read first.

    Raises:
        OptionInputError: If ``entry_prices`` is missing any leg.
    """
    total = Decimal("0")
    for leg in strategy.legs:
        symbol = occ_symbol(leg.contract)
        if symbol not in entry_prices:
            raise OptionInputError(f"Missing entry price for leg '{symbol}'.")
        total -= entry_prices[symbol] * leg.contract.multiplier * signed_quantity(leg)
    return total


def net_greeks(strategy: OptionStrategy, greeks_by_symbol: Mapping[str, Greeks]) -> Greeks:
    """The strategy's Greeks, summed across legs with multiplier and sign.

    Each leg's per-unit Greek is scaled by its multiplier and its signed
    contract count, so a short leg subtracts and a 100-multiplier leg counts a
    hundred times a one-multiplier leg. The result is per-strategy rather than
    per-unit, which is the only form in which two legs can be added at all.

    The caller supplies the Greeks rather than this function computing them:
    each leg may need its own volatility, and
    :func:`alphalab.options.pricing.black_scholes_greeks` takes one per call.
    Computing them here would require choosing a volatility for every strike,
    which is precisely what a surface is for.

    Raises:
        OptionInputError: If ``greeks_by_symbol`` is missing any leg.
    """
    delta = gamma = theta = vega = rho = Decimal("0")
    for leg in strategy.legs:
        symbol = occ_symbol(leg.contract)
        if symbol not in greeks_by_symbol:
            raise OptionInputError(f"Missing Greeks for leg '{symbol}'.")
        scale = signed_quantity(leg) * leg.contract.multiplier
        per_unit = greeks_by_symbol[symbol]
        delta += Decimal(str(per_unit.delta)) * scale
        gamma += Decimal(str(per_unit.gamma)) * scale
        theta += Decimal(str(per_unit.theta)) * scale
        vega += Decimal(str(per_unit.vega)) * scale
        rho += Decimal(str(per_unit.rho)) * scale
    return Greeks(
        delta=float(delta),
        gamma=float(gamma),
        theta=float(theta),
        vega=float(vega),
        rho=float(rho),
    )
