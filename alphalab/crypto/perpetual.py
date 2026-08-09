"""Perpetual-specific mechanics: mark-price mark-to-market and liquidation price.

Unrealized P&L math itself is not reinvented here -- `alphalab.portfolio.position.Position`
already has `unrealized_pnl` and `update_market_price`. The one thing genuinely
specific to perpetuals is that `market_price` must be the *mark* price (an
index-anchored reference price used to prevent manipulation-driven liquidations),
not the last traded price. `mark_to_market` exists to make that convention
impossible to get wrong by accident, not to duplicate Position's math.
"""

from decimal import Decimal

from alphalab.crypto.exceptions import CryptoInputError
from alphalab.portfolio.position import Position
from alphalab.portfolio.types import PositionSide


def mark_to_market(
    position: Position, mark_price: Decimal, timestamp: float
) -> tuple[Position, Decimal]:
    """Updates a perpetual position's market_price to the given mark price.

    Returns the updated position and its resulting unrealized P&L. This is a thin,
    intent-documenting wrapper -- the underlying computation is entirely
    `Position.update_market_price` and `Position.unrealized_pnl`, unmodified.
    """
    updated = position.update_market_price(mark_price, timestamp)
    return updated, updated.unrealized_pnl


def compute_liquidation_price(
    entry_price: Decimal,
    side: PositionSide,
    leverage: Decimal,
    maintenance_margin_rate: Decimal,
) -> Decimal:
    """Computes the approximate isolated-margin liquidation price for a position.

    Uses the standard simplified formula:
        LONG:  entry_price * (1 - 1/leverage + maintenance_margin_rate)
        SHORT: entry_price * (1 + 1/leverage - maintenance_margin_rate)

    This ignores trading fees and funding accrued since entry, and assumes isolated
    (not cross) margin -- a simplification, not a substitute for an exchange's exact
    liquidation engine, which typically also accounts for fees and any unrealized
    funding.

    Raises:
        CryptoInputError: If entry_price or leverage are not positive, side is
            FLAT, or maintenance_margin_rate is negative.
    """
    if entry_price <= Decimal("0"):
        raise CryptoInputError(f"entry_price must be positive, got {entry_price}.")
    if leverage <= Decimal("0"):
        raise CryptoInputError(f"leverage must be positive, got {leverage}.")
    if maintenance_margin_rate < Decimal("0"):
        raise CryptoInputError(
            f"maintenance_margin_rate cannot be negative, got {maintenance_margin_rate}."
        )
    if side is PositionSide.FLAT:
        raise CryptoInputError("Cannot compute a liquidation price for a FLAT position.")

    inverse_leverage = Decimal("1") / leverage

    if side is PositionSide.LONG:
        return entry_price * (Decimal("1") - inverse_leverage + maintenance_margin_rate)

    return entry_price * (Decimal("1") + inverse_leverage - maintenance_margin_rate)
