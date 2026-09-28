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
exchange revising a lot size must not re-identify every fill ever recorded. It
lives here, with the other market conventions, because the portfolio books by
it and :mod:`alphalab.conventions` is what the portfolio may read.

What it declares
----------------

``multiplier``
    Value per unit of price per unit of quantity: ``50`` for an E-mini S&P
    future, ``100`` for a US equity option, ``1`` for a share.
``settlement``
    How a position turns into cash -- see :class:`SettlementModel`.
``lot``
    The quantity grid (:class:`~alphalab.conventions.lot.LotSpecification`), or
    ``None`` when none is declared: then any quantity is tradable, which is how
    every instrument was treated before v3.11.
``minimum_notional``
    The smallest order value a venue accepts, in the instrument's currency, or
    ``None``.
``allows_negative_prices``
    Whether the instrument's price may be zero or below -- a crude oil future
    was in April 2020, power and spread instruments routinely are (ledger
    ACC-007). Every other instrument's price is positive, and a non-positive
    one is refused rather than booked.

Nothing here has a default for the multiplier or the settlement: one assumed
wrongly produces a number, not a refusal (the v3.4 rule for market
conventions). Which instruments may go undeclared is
:func:`~alphalab.instrument.economics.economics_for`'s rule.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum, unique
from typing import Any, Final

from alphalab.conventions.exceptions import ConventionInputError
from alphalab.conventions.lot import LotSpecification

__all__ = [
    "CASH_EQUITY",
    "InstrumentEconomics",
    "SettlementModel",
    "economics_from_primitives",
]


@unique
class SettlementModel(StrEnum):
    """How a position in the instrument becomes cash."""

    #: The whole value changes hands at the trade: a buy pays quantity x price
    #: x multiplier, a sale receives it, and the position is worth its mark.
    CASH_EQUITY = "cash_equity"
    #: Nothing but costs changes hands at the trade; the position's gain or loss
    #: is settled in cash every time it is marked (variation margin), so what it
    #: adds to equity beyond that cash is nil. Listed futures.
    FUTURES_VARIATION = "futures_variation"
    #: A premium changes hands at the trade -- quantity x price x multiplier --
    #: and the position is worth its mark, as a cash equity is, times its
    #: multiplier. Listed options, until exercise or expiry.
    OPTION_PREMIUM = "option_premium"
    #: Settled like a future -- gains and losses as cash, at every mark -- and,
    #: separately, funding payments
    #: (:meth:`~alphalab.portfolio.engine.PortfolioEngine.apply_cash_flow`).
    PERPETUAL = "perpetual"

    @property
    def pays_notional(self) -> bool:
        """Whether the trade itself exchanges the position's value for cash."""

        return self in (SettlementModel.CASH_EQUITY, SettlementModel.OPTION_PREMIUM)


@dataclass(frozen=True, slots=True)
class InstrumentEconomics:
    """What one unit of an instrument is worth, costs and settles as. See the module.

    Raises:
        ConventionInputError: If the multiplier is not a positive finite
            number, the settlement is not a :class:`SettlementModel`, the lot
            is not a :class:`~alphalab.conventions.lot.LotSpecification`, or the
            minimum notional is not a non-negative finite one.
    """

    multiplier: Decimal
    settlement: SettlementModel
    lot: LotSpecification | None = None
    minimum_notional: Decimal | None = None
    allows_negative_prices: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.multiplier, Decimal) or not self.multiplier.is_finite():
            raise ConventionInputError(
                f"A multiplier is a finite Decimal, got {self.multiplier!r}."
            )
        if self.multiplier <= 0:
            raise ConventionInputError(
                f"A multiplier of {self.multiplier} would make every position worth nothing or "
                "less; it is positive."
            )
        if not isinstance(self.settlement, SettlementModel):
            raise ConventionInputError(
                f"settlement must be a SettlementModel, got {self.settlement!r}."
            )
        if self.lot is not None and not isinstance(self.lot, LotSpecification):
            raise ConventionInputError(f"lot must be a LotSpecification, got {self.lot!r}.")
        if self.minimum_notional is not None and (
            not isinstance(self.minimum_notional, Decimal)
            or not self.minimum_notional.is_finite()
            or self.minimum_notional < 0
        ):
            raise ConventionInputError(
                f"A minimum notional is a non-negative finite Decimal, got "
                f"{self.minimum_notional!r}."
            )
        if not isinstance(self.allows_negative_prices, bool):
            raise ConventionInputError(
                f"allows_negative_prices is a bool, got {self.allows_negative_prices!r}."
            )

    @property
    def is_cash_equity(self) -> bool:
        """Whether this is exactly the economics every instrument had before v3.11."""

        return self == CASH_EQUITY

    def admits_price(self, price: Decimal) -> bool:
        """Whether ``price`` is one this instrument can trade or be marked at."""

        return price.is_finite() and (self.allows_negative_prices or price > 0)


#: A fully paid instrument with a multiplier of one and no quantity grid: what the
#: canonical path assumed of every instrument until v3.11.
CASH_EQUITY: Final = InstrumentEconomics(Decimal("1"), SettlementModel.CASH_EQUITY)


def _decimal(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise ConventionInputError(f"{where} is a decimal written as text, got {value!r}.")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ConventionInputError(f"{where} is not a decimal: {value!r}.") from exc


def economics_from_primitives(value: Any, where: str = "economics") -> InstrumentEconomics:
    """Read :class:`InstrumentEconomics` back from its serialized form.

    The one decoder, so the instrument registry's snapshot and the portfolio's
    -- which records the economics each position was booked by -- read it by
    the same rule.

    Raises:
        ConventionInputError: If a field is missing or malformed, or the
            declaration it spells does not hold.
    """

    if not isinstance(value, Mapping):
        raise ConventionInputError(f"{where} is a mapping, got {type(value).__name__}.")
    missing = {"multiplier", "settlement", "lot", "minimum_notional", "allows_negative_prices"}
    missing -= set(value)
    if missing:
        raise ConventionInputError(f"{where} is missing {sorted(missing)}.")
    lot_value = value["lot"]
    lot: LotSpecification | None = None
    if lot_value is not None:
        if not isinstance(lot_value, Mapping) or set(lot_value) != {
            "lot_size",
            "minimum_quantity",
        }:
            raise ConventionInputError(
                f"{where}.lot is a mapping of lot_size and minimum_quantity, got {lot_value!r}."
            )
        lot = LotSpecification(
            _decimal(lot_value["lot_size"], f"{where}.lot.lot_size"),
            _decimal(lot_value["minimum_quantity"], f"{where}.lot.minimum_quantity"),
        )
    settlement = value["settlement"]
    try:
        model = SettlementModel(settlement)
    except ValueError as exc:
        raise ConventionInputError(
            f"{where}.settlement {settlement!r} is not a settlement model."
        ) from exc
    minimum = value["minimum_notional"]
    return InstrumentEconomics(
        multiplier=_decimal(value["multiplier"], f"{where}.multiplier"),
        settlement=model,
        lot=lot,
        minimum_notional=None if minimum is None else _decimal(minimum, f"{where}.minimum"),
        allows_negative_prices=value["allows_negative_prices"],
    )
