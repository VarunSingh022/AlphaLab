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

from alphalab.conventions.economics import InstrumentEconomics
from alphalab.portfolio.exceptions import InvalidTransactionError

__all__ = [
    "Assignment",
    "CashFlow",
    "CashFlowKind",
    "ContractSettlement",
    "ExchangeLeg",
    "ExchangedHolding",
    "Exercise",
    "HoldingExchange",
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


_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class ExchangeLeg:
    """One instrument a holding is exchanged into, by the issuer's declared terms (FUT-021).

    Attributes:
        asset_id: The instrument received.
        ratio: Units received for each unit held; above zero.
        basis_share: The share of the held position's cost basis it carries,
            in ``[0, 1]`` -- the issuer's or the tax rule's allocation, declared.
        in_lieu_price: The price per unit a fraction of a unit is paid at in
            cash, in the received instrument's currency, or ``None``: a fraction
            the book cannot hold is then refused.
    """

    asset_id: str
    ratio: Decimal
    basis_share: Decimal
    in_lieu_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id:
            raise InvalidTransactionError("An exchange leg names the instrument it delivers.")
        for value, what in ((self.ratio, "ratio"), (self.basis_share, "basis share")):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise InvalidTransactionError(f"A leg's {what} is a finite Decimal, got {value!r}.")
        if self.ratio <= 0:
            raise InvalidTransactionError(f"A leg delivers a positive ratio, got {self.ratio}.")
        if not 0 <= self.basis_share <= _ONE:
            raise InvalidTransactionError(f"A basis share is in [0, 1], got {self.basis_share}.")
        if self.in_lieu_price is not None and (
            not isinstance(self.in_lieu_price, Decimal)
            or not self.in_lieu_price.is_finite()
            or self.in_lieu_price < 0
        ):
            raise InvalidTransactionError(
                f"An in-lieu price is a non-negative Decimal, got {self.in_lieu_price!r}."
            )


@dataclass(frozen=True, slots=True)
class HoldingExchange:
    """A holding exchanged by its issuer for other instruments and cash (ledger FUT-021).

    One declaration for every transformation of a held position: a
    stock-for-stock merger (one leg, the holding closed), a cash-and-stock
    merger (a leg and cash), a cash merger (cash alone), a spin-off (the holding
    retained, a leg distributed), a rights issue (retained, a leg of rights --
    themselves a declared option on the share, exercised or left to expire
    through the derivative lifecycle). The cost basis is apportioned exactly by
    the declared shares: each leg's ``basis_share``, the retained holding's
    ``retained_basis_share`` and the cash's share, the remainder, sum to one;
    a cash part's share is realized against the cash it pays.

    Attributes:
        asset_id: The instrument exchanged.
        legs: What it is exchanged into; distinct instruments, none the one
            exchanged.
        cash_per_unit: Cash paid for each unit held, in ``cash_currency``; a
            holder of a short pays it.
        cash_currency: The cash part's currency, or ``None`` for the holding's own.
        retained_basis_share: The share of the basis the holding keeps when it is
            retained -- a spin-off or a rights issue -- or ``None`` when it is
            exchanged away.
        reference: The issuer's reference for it, for the record.
    """

    asset_id: str
    legs: tuple[ExchangeLeg, ...] = ()
    cash_per_unit: Decimal = Decimal("0")
    cash_currency: str | None = None
    retained_basis_share: Decimal | None = None
    reference: str = ""

    def __post_init__(self) -> None:
        legs = tuple(self.legs)
        object.__setattr__(self, "legs", legs)
        if not isinstance(self.asset_id, str) or not self.asset_id:
            raise InvalidTransactionError("An exchange names the instrument exchanged.")
        if not all(isinstance(leg, ExchangeLeg) for leg in legs):
            raise InvalidTransactionError("An exchange's legs are ExchangeLegs.")
        delivered = [leg.asset_id for leg in legs]
        if len(set(delivered)) != len(delivered) or self.asset_id in delivered:
            raise InvalidTransactionError(
                "An exchange delivers each instrument once, and never the one exchanged."
            )
        if not isinstance(self.cash_per_unit, Decimal) or not self.cash_per_unit.is_finite():
            raise InvalidTransactionError("A cash part is a finite Decimal per unit.")
        if self.cash_per_unit < 0:
            raise InvalidTransactionError(f"A cash part is not negative, got {self.cash_per_unit}.")
        if not legs and not self.cash_per_unit:
            raise InvalidTransactionError("An exchange delivers something: a leg or cash.")
        retained = self.retained_basis_share
        if retained is not None and (
            not isinstance(retained, Decimal) or not 0 <= retained <= _ONE
        ):
            raise InvalidTransactionError(f"A retained basis share is in [0, 1], got {retained!r}.")
        if self.cash_basis_share < 0:
            raise InvalidTransactionError(
                "The declared basis shares exceed the whole basis: the legs' and the retained "
                "holding's shares sum to more than one."
            )
        if not self.cash_per_unit and self.cash_basis_share != 0:
            raise InvalidTransactionError(
                f"The declared basis shares sum to {_ONE - self.cash_basis_share}, not one, and "
                "there is no cash part to carry the rest: basis would be lost."
            )

    @property
    def retained(self) -> bool:
        """Whether the holding stays: a spin-off or a rights issue."""

        return self.retained_basis_share is not None

    @property
    def cash_basis_share(self) -> Decimal:
        """The share of the basis the cash part carries: what the legs and the holding do not."""

        carried = sum((leg.basis_share for leg in self.legs), Decimal("0"))
        return _ONE - carried - (self.retained_basis_share or Decimal("0"))


@dataclass(frozen=True, slots=True)
class ExchangedHolding:
    """One holding an exchange delivers, as the pipeline computed it (FUT-021).

    Attributes:
        asset_id: The instrument.
        quantity: Signed whole units received: negative to a short.
        basis: The cost basis it carries, in ``currency``.
        price: What it is marked at until its next price: its last observed
            price, or its basis per unit when it has none.
        currency: Its currency.
        economics: Its declared economics, or ``None`` for a fully paid unit.
    """

    asset_id: str
    quantity: Decimal
    basis: Decimal
    price: Decimal
    currency: str
    economics: InstrumentEconomics | None = None
