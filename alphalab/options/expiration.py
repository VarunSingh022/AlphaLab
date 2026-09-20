"""What happens at expiry: exercise, assignment, and the cash and units that move.

:func:`alphalab.options.strategy.compute_payoff_at_expiry` answers "what is this
worth at expiry", which is a number. This module answers what actually
*happened*: whether the contract was exercised or abandoned, whether the holder
delivered stock or received cash, and which direction each of those moved in.
Those are different questions, and the second one is where the sign errors live.

Every field of the answer is signed from the **position holder's** point of
view, using the same convention every ``Position`` in AlphaLab uses: a positive
quantity is long, a negative one is short. One expression covers both sides,
which is what stops a short leg being booked as though it were a long one.

Nothing is assumed
------------------

Three facts decide the outcome and none of them is a property of the contract:

* **Settlement style.** A physically settled equity option delivers shares; an
  index option pays the difference in cash. The same strike and expiry on two
  venues can differ, so :class:`SettlementStyle` is required.
* **Whether an in-the-money contract was exercised.** Most clearing houses
  exercise automatically above a threshold and a holder may instruct otherwise,
  so :attr:`ExpirationPolicy.exercise_in_the_money` is required rather than
  assumed true.
* **The settlement price.** The price the contract settles against is published
  by the venue and is frequently not the last trade -- an opening-rotation
  average, a closing auction print. It is supplied.

At the money is not in the money
---------------------------------

A contract whose strike equals the settlement price has zero intrinsic value and
is reported :attr:`Moneyness.AT_THE_MONEY`, which this module treats as *not*
in the money: exercising it moves cash and stock in opposite directions of equal
value and gains nothing. Venues differ on the edge case; representing it as its
own state rather than folding it into one side is what lets a caller apply their
venue's rule instead of inheriting one from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.core.enums import Side
from alphalab.options.contract import OptionContract, occ_symbol
from alphalab.options.enums import OptionType
from alphalab.options.exceptions import OptionInputError
from alphalab.options.strategy import OptionStrategy

__all__ = [
    "ExpirationOutcome",
    "ExpirationPolicy",
    "ExpirationResult",
    "Moneyness",
    "SettlementStyle",
    "intrinsic_value",
    "moneyness",
    "resolve_expiration",
    "resolve_strategy_expiration",
]


class Moneyness(Enum):
    """Where the settlement price sits relative to the strike."""

    #: Intrinsic value is strictly positive.
    IN_THE_MONEY = auto()

    #: The settlement price equals the strike exactly. Intrinsic value is zero,
    #: and this is its own state rather than a kind of out-of-the-money because
    #: venues differ on what happens here.
    AT_THE_MONEY = auto()

    #: Intrinsic value is zero and the strike is on the wrong side.
    OUT_OF_THE_MONEY = auto()


class SettlementStyle(Enum):
    """What moves when a contract is exercised."""

    #: The underlying is delivered against the strike. Shares move, and cash
    #: moves by ``strike * multiplier * contracts``.
    PHYSICAL = auto()

    #: The intrinsic value is paid in cash. No underlying moves. The convention
    #: for index options, and the one that makes an exercised position vanish
    #: rather than become a stock position.
    CASH = auto()


class ExpirationOutcome(Enum):
    """What became of the contract."""

    #: Not in the money at expiry. Nothing moved.
    EXPIRED_WORTHLESS = auto()

    #: In the money, and the long holder exercised. Reported for a long
    #: position.
    EXERCISED = auto()

    #: In the money, and the holder on the other side exercised. Reported for a
    #: short position, which does not make the choice.
    ASSIGNED = auto()

    #: In the money and deliberately not exercised. A real instruction, and
    #: reported distinctly from expiring worthless because the two leave the
    #: same empty position for different reasons.
    ABANDONED = auto()


@dataclass(frozen=True, slots=True)
class ExpirationPolicy:
    """How expiry is resolved, stated rather than inferred.

    Attributes:
        settlement: Physical delivery or cash settlement.
        exercise_in_the_money: Whether an in-the-money contract is exercised.
            For a long position this is the holder's instruction. For a short
            one it is the assumption that the holder on the other side
            exercised -- a short position never decides, and the field stands in
            for what the long side did.
    """

    settlement: SettlementStyle
    exercise_in_the_money: bool


@dataclass(frozen=True, slots=True)
class ExpirationResult:
    """What expiry did to one position, and in what units.

    The two quantities are separate fields because they are separate
    dimensions -- one is money and one is a count of the underlying -- and the
    whole purpose of this type is that they were being reported as one number.

    Attributes:
        contract_symbol: ``occ_symbol`` of the contract.
        outcome: What happened.
        moneyness: Where the settlement price was.
        contracts: The signed contract count this describes, unchanged from the
            input.
        intrinsic_per_unit: Intrinsic value per unit of the underlying, always
            non-negative. Not multiplied by anything.
        cash_flow: Money to the position holder, signed: negative is paid out.
            In the contract's premium currency, which the caller names --
            :class:`~alphalab.options.contract.OptionContract` carries no
            currency and this module invents none.
        underlying_units: Units of the underlying delivered to the holder,
            signed: negative is delivered away. Zero under
            :attr:`SettlementStyle.CASH`, where no underlying moves.
    """

    contract_symbol: str
    outcome: ExpirationOutcome
    moneyness: Moneyness
    contracts: Decimal
    intrinsic_per_unit: Decimal
    cash_flow: Decimal
    underlying_units: Decimal


def intrinsic_value(contract: OptionContract, settlement_price: Decimal) -> Decimal:
    """Intrinsic value per unit of the underlying, never negative."""

    if contract.option_type is OptionType.CALL:
        return max(settlement_price - contract.strike, Decimal("0"))
    return max(contract.strike - settlement_price, Decimal("0"))


def moneyness(contract: OptionContract, settlement_price: Decimal) -> Moneyness:
    """Where ``settlement_price`` sits relative to the strike."""

    if settlement_price == contract.strike:
        return Moneyness.AT_THE_MONEY
    if intrinsic_value(contract, settlement_price) > Decimal("0"):
        return Moneyness.IN_THE_MONEY
    return Moneyness.OUT_OF_THE_MONEY


def resolve_expiration(
    contract: OptionContract,
    contracts: Decimal,
    settlement_price: Decimal,
    policy: ExpirationPolicy,
) -> ExpirationResult:
    """Resolve one position at expiry.

    Args:
        contract: The contract expiring.
        contracts: Signed contract count. Positive is long, negative is short,
            the convention every ``Position`` in AlphaLab uses.
        settlement_price: The price the venue settles against. Supplied, because
            it is published by the venue and is frequently not the last trade.
        policy: How expiry is resolved.

    Raises:
        OptionInputError: If ``settlement_price`` is negative, or ``contracts``
            is zero -- there is no position to resolve, and reporting one as
            expiring worthless would assert something about a holding that does
            not exist.
    """

    if settlement_price < Decimal("0"):
        raise OptionInputError(
            f"settlement_price is {settlement_price}. A negative settlement price inverts "
            "every intrinsic value here and is not assumed."
        )
    if contracts == Decimal("0"):
        raise OptionInputError(
            "contracts is zero; there is no position to resolve at expiry. Reporting it as "
            "worthless would state an outcome for a holding that was never open."
        )

    symbol = occ_symbol(contract)
    where = moneyness(contract, settlement_price)
    intrinsic = intrinsic_value(contract, settlement_price)
    multiplier = Decimal(contract.multiplier)

    if where is not Moneyness.IN_THE_MONEY:
        return ExpirationResult(
            contract_symbol=symbol,
            outcome=ExpirationOutcome.EXPIRED_WORTHLESS,
            moneyness=where,
            contracts=contracts,
            intrinsic_per_unit=intrinsic,
            cash_flow=Decimal("0"),
            underlying_units=Decimal("0"),
        )

    if not policy.exercise_in_the_money:
        return ExpirationResult(
            contract_symbol=symbol,
            outcome=ExpirationOutcome.ABANDONED,
            moneyness=where,
            contracts=contracts,
            intrinsic_per_unit=intrinsic,
            cash_flow=Decimal("0"),
            underlying_units=Decimal("0"),
        )

    outcome = (
        ExpirationOutcome.EXERCISED if contracts > Decimal("0") else ExpirationOutcome.ASSIGNED
    )

    if policy.settlement is SettlementStyle.CASH:
        return ExpirationResult(
            contract_symbol=symbol,
            outcome=outcome,
            moneyness=where,
            contracts=contracts,
            intrinsic_per_unit=intrinsic,
            cash_flow=intrinsic * multiplier * contracts,
            underlying_units=Decimal("0"),
        )

    # Physical delivery. A call takes delivery of the underlying and pays the
    # strike; a put delivers it and receives the strike. Multiplying by the
    # signed contract count flips both for a short position, so an assignment is
    # the same expression read from the other side rather than a second branch.
    direction = Decimal("1") if contract.option_type is OptionType.CALL else Decimal("-1")
    return ExpirationResult(
        contract_symbol=symbol,
        outcome=outcome,
        moneyness=where,
        contracts=contracts,
        intrinsic_per_unit=intrinsic,
        cash_flow=-direction * contract.strike * multiplier * contracts,
        underlying_units=direction * multiplier * contracts,
    )


def resolve_strategy_expiration(
    strategy: OptionStrategy, settlement_price: Decimal, policy: ExpirationPolicy
) -> tuple[ExpirationResult, ...]:
    """Resolve every leg of a multi-leg strategy, in declaration order.

    Each leg keeps its own quantity and direction:
    :class:`~alphalab.options.strategy.OptionLeg` carries a positive quantity
    and a :class:`~alphalab.core.enums.Side`, and the signed count handed to
    :func:`resolve_expiration` is ``+quantity`` for ``BUY`` and ``-quantity``
    for ``SELL``. Nothing is netted: two legs on the same contract in opposite
    directions are two results, because a spread that is assigned on one side
    and exercised on the other is a real event with two legs to book, and a
    netted zero would hide it.
    """

    return tuple(
        resolve_expiration(
            leg.contract,
            Decimal(leg.quantity) if leg.side is Side.BUY else -Decimal(leg.quantity),
            settlement_price,
            policy,
        )
        for leg in strategy.legs
    )
