"""What happens to a holding without a trade: splits and cash flows (ledger ACC-006).

Until v3.11 the portfolio could deposit, withdraw, convert and fill, and nothing
else: a split, a dividend, interest on cash, a borrow fee or a perpetual's
funding payment could not be booked, so a backtest over unadjusted prices
mis-stated its P&L at every split and total-return accounting was possible only
through adjusted prices.

Two declarations, each applied by one
:class:`~alphalab.portfolio.engine.PortfolioEngine` method:

* :class:`Split` -- a split, a reverse split or a stock dividend. It changes a
  position's quantity by a ratio and leaves its value exactly where it was: the
  cost basis is untouched, the average cost and the mark are divided by the
  ratio. Fractions are kept exactly; a venue paying cash in lieu of a fraction
  pays it as a sale, which the caller books.
* :class:`CashFlow` -- cash a holding earned or cost: a dividend (paid *by* a
  short, so negative there), interest, a fee, funding. It moves cash and is
  realized P&L, so the accounting identity holds with it in.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique

from alphalab.portfolio.exceptions import InvalidTransactionError

__all__ = ["CashFlow", "CashFlowKind", "Split"]


@unique
class CashFlowKind(StrEnum):
    """What a :class:`CashFlow` is."""

    #: A cash dividend or distribution: received on a long, paid on a short.
    DIVIDEND = "dividend"
    #: Interest on cash or margin: received or paid.
    INTEREST = "interest"
    #: A fee the account is charged: borrow, custody, exchange, a withholding.
    FEE = "fee"
    #: A perpetual swap's funding payment: paid or received each interval.
    FUNDING = "funding"


@dataclass(frozen=True, slots=True)
class CashFlow:
    """Cash a holding earned or cost, without a trade.

    Attributes:
        kind: What it is.
        amount: Signed, in ``currency``: received is positive, paid negative.
            Booked exactly as given, rounded once to the currency's minor unit.
        currency: What it is in.
        asset_id: The holding it arose from, or ``""`` for the account itself
            -- interest on cash, an account fee.
        reference: The caller's reference, not interpreted.

    Raises:
        InvalidTransactionError: If the amount is not a finite non-zero
            number, or the currency is blank.
    """

    kind: CashFlowKind
    amount: Decimal
    currency: str
    asset_id: str = ""
    reference: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CashFlowKind):
            raise InvalidTransactionError(f"kind must be a CashFlowKind, got {self.kind!r}.")
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise InvalidTransactionError(f"A cash flow is a finite Decimal, got {self.amount!r}.")
        if self.amount == 0:
            raise InvalidTransactionError(
                "A cash flow of zero moves nothing; there is nothing to book."
            )
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise InvalidTransactionError("A cash flow must name the currency it is in.")


@dataclass(frozen=True, slots=True)
class Split:
    """A split, reverse split or stock dividend of one holding.

    Attributes:
        asset_id: The holding.
        ratio: Units after for each unit before: ``Decimal("2")`` for two-for-
            one, ``Decimal("0.1")`` for one-for-ten, ``Decimal("1.05")`` for a
            5% stock dividend.

    Raises:
        InvalidTransactionError: If the ratio is not a finite positive number,
            or is one -- which changes nothing and is more likely a mistake
            than an action.
    """

    asset_id: str
    ratio: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise InvalidTransactionError("A split names the holding it applies to.")
        if not isinstance(self.ratio, Decimal) or not self.ratio.is_finite() or self.ratio <= 0:
            raise InvalidTransactionError(
                f"A split's ratio is a finite positive Decimal, got {self.ratio!r}."
            )
        if self.ratio == 1:
            raise InvalidTransactionError(
                "A split with a ratio of one changes nothing; it is refused as a likely mistake."
            )
