"""What trading one unit of an instrument means for cash and value (ledger ACC-005).

Until v3.11 the canonical path booked every fill as a fully paid cash equity:
one unit, bought at a price, cost that price in cash and was worth that price
thereafter. That is right for a share and wrong by a factor for everything else
-- a futures contract on 50 units of an index is worth 50 times its quoted
price and costs nothing but margin to buy, an option is paid for in premium
times its multiplier, a perpetual swap settles its profit continuously -- and
the derivatives packages that knew this were analytics only, beside a path
that did not.

:class:`InstrumentEconomics` is the declaration the path reads instead of
assuming. It rides on the instrument's
:class:`~alphalab.instrument.record.InstrumentRecord`, outside the identity key:
economics describe an instrument without changing what it *is*, and an
exchange revising a lot size must not re-identify every fill ever recorded.

What it declares
----------------

``multiplier``
    Value per unit of price per unit of quantity: ``50`` for an E-mini S&P
    future, ``100`` for a US equity option, ``1`` for a share.
``settlement``
    How a position turns into cash -- see :class:`SettlementStyle`.
``lot``
    The quantity grid (:class:`~alphalab.conventions.lot.LotSpecification`), or
    ``None`` when none is declared: then any quantity is tradable, which is how
    every instrument was treated before v3.11.
``minimum_notional``
    The smallest order value a venue accepts, in the instrument's currency, or
    ``None``.
``allows_negative_prices``
    Whether the instrument's price may go below zero -- a crude oil future did
    in April 2020, power and spread instruments routinely do (ledger ACC-007).

Declared, or refused
--------------------

Nothing here has a default: a multiplier or a settlement style assumed wrongly
produces a number, not a refusal (the v3.4 rule for market conventions). A
record that declares no economics is read by :func:`economics_for` -- as a cash
equity with a multiplier of one and no grid when that is what its asset type
*is* (an equity, spot crypto, a currency, cash), which is exactly how every such
instrument was booked before v3.11; and refused for a future or an option,
whose multiplier and settlement nothing can supply. Until v3.11 those were
booked as cash equities too, wrong by their multiplier (ledger ACC-005).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique
from typing import Final

from alphalab.conventions.lot import LotSpecification
from alphalab.core.enums import AssetType
from alphalab.instrument.exceptions import InstrumentInputError

__all__ = [
    "CASH_EQUITY",
    "InstrumentEconomics",
    "SettlementStyle",
    "economics_for",
]


@unique
class SettlementStyle(StrEnum):
    """How a position in the instrument becomes cash."""

    #: The whole value changes hands at the trade: a buy pays quantity x price
    #: x multiplier, a sale receives it, and the position is worth its mark.
    CASH_EQUITY = "cash_equity"
    #: Nothing but costs changes hands at the trade; the position's gain or loss
    #: is settled in cash every time it is marked (variation margin), so its
    #: open value is always nil. Listed futures.
    FUTURES_VARIATION = "futures_variation"
    #: A premium changes hands at the trade -- quantity x price x multiplier --
    #: and the position is worth its mark, as a cash equity is, times its
    #: multiplier. Listed options, until exercise or expiry.
    OPTION_PREMIUM = "option_premium"
    #: Settled like a future -- gains and losses as cash, continuously -- and,
    #: separately, funding payments (an
    #: :meth:`~alphalab.portfolio.engine.PortfolioEngine.apply_cash_flow`).
    PERPETUAL = "perpetual"

    @property
    def pays_notional(self) -> bool:
        """Whether the trade itself exchanges the position's value for cash."""

        return self in (SettlementStyle.CASH_EQUITY, SettlementStyle.OPTION_PREMIUM)


@dataclass(frozen=True, slots=True)
class InstrumentEconomics:
    """What one unit of an instrument is worth, costs and settles as. See the module.

    Raises:
        InstrumentInputError: If the multiplier is not a positive finite
            number, or the minimum notional is not a non-negative finite one.
    """

    multiplier: Decimal
    settlement: SettlementStyle
    lot: LotSpecification | None = None
    minimum_notional: Decimal | None = None
    allows_negative_prices: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.multiplier, Decimal) or not self.multiplier.is_finite():
            raise InstrumentInputError(
                f"A multiplier is a finite Decimal, got {self.multiplier!r}."
            )
        if self.multiplier <= 0:
            raise InstrumentInputError(
                f"A multiplier of {self.multiplier} would make every position worth nothing or "
                "less; it is positive."
            )
        if self.minimum_notional is not None and (
            not isinstance(self.minimum_notional, Decimal)
            or not self.minimum_notional.is_finite()
            or self.minimum_notional < 0
        ):
            raise InstrumentInputError(
                f"A minimum notional is a non-negative finite Decimal, got "
                f"{self.minimum_notional!r}."
            )

    @property
    def is_cash_equity(self) -> bool:
        """Whether this is exactly the economics every instrument had before v3.11."""

        return self == CASH_EQUITY


#: A fully paid instrument with a multiplier of one and no quantity grid: what the
#: canonical path assumed of every instrument until v3.11.
CASH_EQUITY: Final = InstrumentEconomics(Decimal("1"), SettlementStyle.CASH_EQUITY)

#: The asset types that *are* cash equities when nothing else is declared.
_FULLY_PAID: Final = frozenset(
    {AssetType.EQUITY, AssetType.CRYPTO, AssetType.FOREX, AssetType.CASH}
)


def economics_for(
    asset_type: AssetType, declared: InstrumentEconomics | None, name: str = "the instrument"
) -> InstrumentEconomics:
    """The economics an instrument is booked by: what it declared, or what its type is.

    Raises:
        InstrumentInputError: If nothing is declared for a future or an option,
            whose multiplier and settlement no default can supply.
    """

    if declared is not None:
        return declared
    if asset_type in _FULLY_PAID:
        return CASH_EQUITY
    raise InstrumentInputError(
        f"{name} is a {asset_type.value}, and declares no economics: its multiplier and "
        "settlement cannot be assumed, so it cannot be booked. Declare InstrumentEconomics "
        "on its InstrumentRecord."
    )
