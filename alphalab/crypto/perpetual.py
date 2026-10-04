"""Perpetual-specific mechanics: mark-price mark-to-market and liquidation price.

The liquidation price is exact for the terms it is given -- which notional
maintenance is charged on, the fees and the funding -- since v3.13; see
:func:`compute_liquidation_price`.

Unrealized P&L math itself is not reinvented here -- `alphalab.portfolio.position.Position`
already has `unrealized_pnl` and `update_market_price`. The one thing genuinely
specific to perpetuals is that `market_price` must be the *mark* price (an
index-anchored reference price used to prevent manipulation-driven liquidations),
not the last traded price. `mark_to_market` exists to make that convention
impossible to get wrong by accident, not to duplicate Position's math.
"""

from decimal import Decimal
from enum import Enum, auto
from typing import Final

from alphalab.common.arithmetic import in_accounting_context
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


class MaintenanceBasis(Enum):
    """The notional a venue charges maintenance margin on.

    The two conventions venues use, and they give different liquidation prices
    for the same position: on ``ENTRY_NOTIONAL`` the requirement is fixed when
    the position opens; on ``MARK_NOTIONAL`` it moves with the mark price.
    Which one a venue uses is the venue's to say, so it is stated here, never
    assumed.
    """

    ENTRY_NOTIONAL = auto()
    MARK_NOTIONAL = auto()


_ZERO: Final = Decimal("0")
_ONE: Final = Decimal("1")


def _finite(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise CryptoInputError(f"{name} must be a finite Decimal, got {value!r}.")
    return value


@in_accounting_context
def compute_liquidation_price(
    entry_price: Decimal,
    side: PositionSide,
    leverage: Decimal,
    maintenance_margin_rate: Decimal,
    *,
    basis: MaintenanceBasis,
    quantity: Decimal,
    fees: Decimal,
    funding: Decimal,
) -> Decimal | None:
    """The mark price at which an isolated linear perpetual position is liquidated.

    The position posts ``quantity * entry_price / leverage`` as isolated margin.
    Its equity at a mark price ``M`` is that margin, plus the move
    ``quantity * (M - entry_price)`` (negated for a short), plus ``funding``,
    less ``fees``; it is liquidated where that equity equals the maintenance
    margin -- ``maintenance_margin_rate`` times the notional ``basis`` names.
    Solved exactly, with ``s`` = +1 for a long and -1 for a short and ``IM``
    the posted margin::

        ENTRY_NOTIONAL: M = E + s * (m * Q * E - IM - funding + fees) / Q
        MARK_NOTIONAL: M = (s * Q * E - IM - funding + fees) / (Q * (s - m))

    With no fees and no funding these are the familiar ``E(1 - 1/L + m)`` and
    ``E(1 + 1/L - m)`` on entry notional, and ``E(1 - 1/L)/(1 - m)`` and
    ``E(1 + 1/L)/(1 + m)`` on mark notional. Until v3.13 the function returned
    the entry-notional figure for every venue, called it "the standard
    simplified formula", and ignored fees and funding.

    What a venue's own liquidation engine adds stays the venue's: tiered
    maintenance rates, a mark price smoothed by its index, insurance funds,
    auto-deleveraging, cross margin. This is the figure for exactly the terms
    it is given.

    Args:
        entry_price: The position's average entry price.
        side: ``LONG`` or ``SHORT``.
        leverage: Notional over posted margin.
        maintenance_margin_rate: The maintenance rate, a fraction of notional.
        basis: Which notional the rate is charged on.
        quantity: The position's size, in units of the base asset.
        fees: Fees paid or reserved against the position's margin, in the
            quote currency. Not negative.
        funding: Funding accrued since entry, in the quote currency: positive
            when received, negative when paid.

    Returns:
        The liquidation price, or ``None`` for a long whose equity covers its
        maintenance margin at every positive price -- a long at low enough
        leverage cannot be liquidated by a fall in price.

    Raises:
        CryptoInputError: If a number is not finite, ``entry_price``,
            ``leverage`` or ``quantity`` is not positive, ``side`` is ``FLAT``,
            the maintenance rate is negative or at least one, ``fees`` is
            negative, ``basis`` is not a :class:`MaintenanceBasis`, or the
            position's equity is already at or below its maintenance margin at
            entry -- it would be liquidated the moment it opened.
    """

    entry, lev = _finite(entry_price, "entry_price"), _finite(leverage, "leverage")
    rate, size = (
        _finite(maintenance_margin_rate, "maintenance_margin_rate"),
        _finite(quantity, "quantity"),
    )
    paid, accrued = _finite(fees, "fees"), _finite(funding, "funding")
    if entry <= _ZERO:
        raise CryptoInputError(f"entry_price must be positive, got {entry}.")
    if lev <= _ZERO:
        raise CryptoInputError(f"leverage must be positive, got {lev}.")
    if size <= _ZERO:
        raise CryptoInputError(f"quantity must be positive, got {size}.")
    if not _ZERO <= rate < _ONE:
        raise CryptoInputError(
            f"maintenance_margin_rate must lie in [0, 1), got {rate}: it is a fraction of notional."
        )
    if paid < _ZERO:
        raise CryptoInputError(f"fees cannot be negative, got {paid}.")
    if side is PositionSide.FLAT:
        raise CryptoInputError("Cannot compute a liquidation price for a FLAT position.")
    if not isinstance(basis, MaintenanceBasis):
        raise CryptoInputError(f"basis must be a MaintenanceBasis, got {basis!r}.")

    posted = size * entry / lev
    if posted + accrued - paid <= rate * size * entry:
        raise CryptoInputError(
            "The position's equity at entry is at or below its maintenance margin: it would be "
            "liquidated the moment it opened."
        )
    sign = _ONE if side is PositionSide.LONG else -_ONE
    if basis is MaintenanceBasis.ENTRY_NOTIONAL:
        price = entry + sign * (rate * size * entry - posted - accrued + paid) / size
    else:
        price = (sign * size * entry - posted - accrued + paid) / (size * (sign - rate))
    return price if price > _ZERO else None
