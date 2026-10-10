"""The portfolio's monetary precision policy -- one place, one rule.

Contract
--------
1. **Money is exact at its currency's minor unit.** Every monetary amount stored
   in :class:`~alphalab.portfolio.engine.PortfolioState` -- cash, cost basis,
   realized P&L, commissions, market value -- is a whole number of the minor
   unit of the currency it is in: a cent for USD, a yen for JPY, a fils for KWD,
   and whatever a caller declared for a currency outside ISO 4217 (see
   :mod:`alphalab.common.currency_units`). :func:`to_money` is where that
   rounding happens.
2. **Rounding happens once, by whoever produces the amount.** A fill's notional
   and commission are rounded to money as they enter the portfolio, and both the
   cash movement and the position's cost basis are then derived from those same
   rounded values. Nothing downstream rounds again: everything downstream is
   exact Decimal addition and subtraction over values that are already exact.
   :class:`~alphalab.portfolio.cash.CashLedger` and
   :class:`~alphalab.portfolio.amounts.CurrencyAmounts` store what they are
   given and round nothing.
3. **Prices and quantities are not money, and are never rounded.** A venue
   price of ``0.00001234`` and a fill of ``0.0000005`` BTC are booked as the
   venue reported them. Until v3.10 every price was quantized to four decimal
   places and every quantity to six before any money was computed: a
   1,000,000-unit EURUSD purchase at ``1.08345`` was booked at
   ``1,083,400.00`` instead of ``1,083,450.00``, and a real sub-micro fill was
   refused as a zero-quantity one. Lot and tick sizes belong to sizing and to
   the venue's conventions (:mod:`alphalab.conventions`), never to accounting.
4. **Arithmetic runs in the pinned context.** Products and quotients are
   computed in :data:`~alphalab.common.arithmetic.ACCOUNTING_CONTEXT`, so the
   caller's ``decimal.getcontext()`` cannot change a book.

Why this matters
----------------
Before this policy the cash ledger rounded ``quantity * price + commission`` while
the position independently rounded ``(exit_price - average_cost) * quantity``.
Two independent roundings of the same economic event disagree by up to half a
minor unit each, and the error accumulated.

Because every amount now comes from :func:`to_money` exactly once, and because a
split amount is always derived by *subtracting* one exact part from an exact
whole rather than by rounding each part, the accounting identity

    equity == deposits - withdrawals + realized_pnl + unrealized_pnl
              - commission_paid

is exact -- not approximately, but as an identity over exact Decimal values --
per currency, for any price and quantity the engine accepts.
"""

from decimal import Decimal
from typing import Final

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.common.currency_units import STANDARD_CURRENCY_UNITS, CurrencyUnits

__all__ = ["ZERO_MONEY", "notional", "to_money"]

#: Zero, in no particular currency. ``Decimal("0")`` rather than ``0.00``: added
#: to a yen amount it must not give the yen two decimal places it does not have.
ZERO_MONEY: Final = Decimal("0")


def to_money(
    amount: Decimal, currency: str, units: CurrencyUnits = STANDARD_CURRENCY_UNITS
) -> Decimal:
    """Round ``amount`` half-even to ``currency``'s minor unit.

    Args:
        amount: The figure to round.
        currency: What it is denominated in. Required: a minor unit is a
            property of a currency, and there is no default one.
        units: The minor units in force. ISO 4217 unless the book declared more.

    Raises:
        UnknownCurrencyUnitsError: If ``currency`` is neither an ISO 4217 code
            with a minor unit nor declared in ``units``.
    """

    return units.round(amount, currency)


def notional(
    quantity: Decimal,
    price: Decimal,
    currency: str,
    units: CurrencyUnits = STANDARD_CURRENCY_UNITS,
) -> Decimal:
    """Money value of ``|quantity|`` units at ``price``, in ``currency``.

    This is *the* definition of a trade's notional. The cash ledger and the
    position cost basis both call it with the same arguments, so they can never
    disagree about how much money the trade moved.
    """

    return units.round(ACCOUNTING_CONTEXT.multiply(abs(quantity), price), currency)
