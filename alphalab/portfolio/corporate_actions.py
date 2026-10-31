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

__all__ = [
    "Assignment",
    "CashFlow",
    "CashFlowKind",
    "ContractSettlement",
    "Exercise",
    "OptionExpiry",
    "Split",
]


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


@dataclass(frozen=True, slots=True)
class ContractSettlement:
    """The final cash settlement of a future or an option at its expiry (v4.1.1, FUT-012).

    What an exchange publishes once a contract has stopped trading: the price
    every open position in it is closed at. A cash-settled future's last
    variation is settled at it; a cash-settled option is exercised at its
    intrinsic value per unit -- ``Decimal("0")`` for one expiring worthless.
    For a future whose declared
    :class:`~alphalab.instrument.terms.FutureTerms` are physical, the deliverable
    then moves against this price (v4.1.1, FUT-013). An option whose
    :class:`~alphalab.instrument.terms.OptionTerms` are declared is not settled
    at a stated price but by :class:`OptionExpiry`, which the engine resolves
    from its terms.

    Attributes:
        asset_id: The contract.
        price: The final settlement price per unit, as the exchange published it.
        reference: The caller's reference, not interpreted.

    Raises:
        InvalidTransactionError: If the contract is not named or the price is
            not a finite Decimal.
    """

    asset_id: str
    price: Decimal
    reference: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise InvalidTransactionError("A settlement names the contract it settles.")
        if not isinstance(self.price, Decimal) or not self.price.is_finite():
            raise InvalidTransactionError(
                f"A settlement price is a finite Decimal, got {self.price!r}."
            )


def _contracts(value: Decimal, what: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise InvalidTransactionError(f"{what} is a positive count of contracts, got {value!r}.")
    if value != value.to_integral_value():
        raise InvalidTransactionError(
            f"{what} is {value}; a contract is exercised or assigned whole."
        )


def _settlement_value(value: Decimal | None, required: bool, what: str) -> None:
    if value is None:
        if required:
            raise InvalidTransactionError(f"{what} needs the underlying's settlement value.")
        return
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise InvalidTransactionError(
            f"{what}'s underlying settlement value is a non-negative finite Decimal, got {value!r}."
        )


@dataclass(frozen=True, slots=True)
class Exercise:
    """A holder's instruction to exercise ``contracts`` of a long option, effective at its instant.

    The decision is the holder's -- the engine checks the contract allows it then,
    and carries out what the contract's
    :class:`~alphalab.instrument.terms.OptionTerms` say happens; it does not judge
    whether exercising was wise (v4.1.1, FUT-013).

    Attributes:
        asset_id: The option.
        contracts: How many, whole.
        underlying_value: The value the exercise settles against, per unit of
            the deliverable, as the venue publishes it -- required for a
            cash-settled contract, and ``None`` for a physical one, which needs
            no price: the deliverable moves against the strike.
        reference: The caller's reference, not interpreted.
    """

    asset_id: str
    contracts: Decimal
    underlying_value: Decimal | None = None
    reference: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise InvalidTransactionError("An exercise names the option it exercises.")
        _contracts(self.contracts, "An exercise")
        _settlement_value(self.underlying_value, False, "An exercise")


@dataclass(frozen=True, slots=True)
class Assignment:
    """``contracts`` of a short option assigned at its instant (v4.1.1, FUT-013).

    Assignment is the clearing house's notice that holders on the other side
    exercised. It is stated, never drawn: which written contracts are assigned
    before expiry is not knowable from prices, so an early assignment is an
    input a caller supplies -- recorded history, or a policy the researcher
    states. At expiry the contract's declared automatic-exercise rule assigns.

    Attributes:
        asset_id: The option.
        contracts: How many, whole.
        underlying_value: As :attr:`Exercise.underlying_value`.
        reference: The caller's reference, not interpreted.
    """

    asset_id: str
    contracts: Decimal
    underlying_value: Decimal | None = None
    reference: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise InvalidTransactionError("An assignment names the option assigned.")
        _contracts(self.contracts, "An assignment")
        _settlement_value(self.underlying_value, False, "An assignment")


@dataclass(frozen=True, slots=True)
class OptionExpiry:
    """An option reaching expiry, with the value it settles against (v4.1.1, FUT-013).

    Every position still open is resolved by the contract's terms: exercised or
    assigned where its automatic-exercise rule says so -- in cash at the
    intrinsic value, or by delivery against the strike -- and otherwise closed
    at nothing, as an expired option is worth.

    Attributes:
        asset_id: The option.
        underlying_value: The deliverable's settlement value per unit, as the
            venue publishes it -- often not the last trade. Required for a contract
            on one deliverable; ``None`` for one on a package -- a basket or a cash
            component -- whose components are valued at the prices the run has
            observed for them (v4.2.0, FUT-016).
        reference: The caller's reference, not interpreted.
    """

    asset_id: str
    underlying_value: Decimal | None = None
    reference: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise InvalidTransactionError("An expiry names the option expiring.")
        _settlement_value(self.underlying_value, False, "An expiry")
