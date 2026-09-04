"""The portfolio's monetary precision policy -- one place, one rule.

Contract
--------
1. **Money is exact at the currency's minor unit.** Every monetary amount stored
   in :class:`~alphalab.portfolio.engine.PortfolioState` -- cash, cost basis,
   realized P&L, commissions, market value -- is an exact multiple of
   ``0.01``. :func:`to_money` is the *only* place that rounding happens.
2. **Rounding happens once, at entry.** A fill's notional and commission are
   rounded to money as they enter the portfolio, and both the cash movement and
   the position's cost basis are then derived from those same rounded values.
   Nothing downstream rounds again, because everything downstream is exact
   Decimal addition and subtraction over values that are already exact.
3. **Prices and quantities are inputs, not money.** They keep their own, finer
   precision (:data:`PRICE_QUANT`, :data:`SHARE_QUANT`); they are rounded to
   money only at the moment they are multiplied into an amount.

Why this matters
----------------
Before this policy the cash ledger rounded ``quantity * price + commission`` while
the position independently rounded ``(exit_price - average_cost) * quantity``.
Two independent roundings of the same economic event disagree by up to half a
cent each, and the error accumulated: an ordinary penny-spread quote
(bid 100.00 / ask 100.01, mid 100.005) put the portfolio accounting identity out
by a cent, and randomized multi-asset portfolios drifted by up to five.

Because every amount now comes from :func:`to_money` exactly once, and because a
split amount is always derived by *subtracting* one exact part from an exact
whole rather than by rounding each part, the accounting identity

    equity == deposits - withdrawals + realized_pnl + unrealized_pnl
              - commission_paid

is exact -- not approximately, but as an identity over exact Decimal values --
for any price and quantity the engine accepts.
"""

from decimal import Decimal

#: Currency minor unit. Every stored monetary amount is a multiple of this.
CURRENCY_QUANT = Decimal("0.01")

#: Price precision. Prices are inputs to money, not money themselves.
PRICE_QUANT = Decimal("0.0001")

#: Share/quantity precision.
SHARE_QUANT = Decimal("0.000001")

ZERO_MONEY = Decimal("0.00")


def to_money(amount: Decimal) -> Decimal:
    """Round ``amount`` to the currency minor unit. The only rounding point."""

    return amount.quantize(CURRENCY_QUANT)


def to_price(price: Decimal) -> Decimal:
    """Round ``price`` to the supported price precision."""

    return price.quantize(PRICE_QUANT)


def to_quantity(quantity: Decimal) -> Decimal:
    """Round ``quantity`` to the supported share precision."""

    return quantity.quantize(SHARE_QUANT)


def notional(quantity: Decimal, price: Decimal) -> Decimal:
    """Money value of ``|quantity|`` units at ``price``.

    This is *the* definition of a trade's notional. The cash ledger and the
    position cost basis both call it with the same arguments, so they can never
    disagree about how much money the trade moved.
    """

    return to_money(abs(quantity) * price)
