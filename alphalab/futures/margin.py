"""Futures margin: an exchange's number, carried rather than computed.

A futures position is not bought, it is *margined*. The initial and maintenance
requirements are set per contract by the clearing house, revised without notice
when volatility moves, reduced for a calendar spread by a published credit, and
stated as an amount of money per contract rather than as a percentage of
notional. None of that is derivable from a price.

So nothing here computes a requirement. :class:`ContractMarginSpec` is what an
exchange published, and :func:`position_margin` multiplies it by a contract
count. The value of the module is that the dependency is now **visible**: a
research path that needs margin and has none gets
:class:`~alphalab.futures.exceptions.FuturesInputError` naming the contract,
rather than a plausible number from an invented rate.

Three margins, three owners
---------------------------

* :class:`alphalab.portfolio.margin.MarginEngine` is an *account* calculation --
  buying power and margin remaining against a book's equity, at a rate the
  caller states (v2.17 made that rate required for this very reason).
* :func:`alphalab.crypto.perpetual.compute_liquidation_price` is a *price* --
  where an isolated-margin perpetual is closed out.
* This is a *per-contract requirement* in money, which neither of the others
  holds and neither can derive.

``tests/regression/test_shared_names_stay_distinct.py`` records why they are
three things.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from alphalab.futures.contract import FutureContract, futures_symbol
from alphalab.futures.exceptions import FuturesInputError

__all__ = ["ContractMarginSpec", "MarginPosture", "position_margin"]


@dataclass(frozen=True, slots=True)
class ContractMarginSpec:
    """What a clearing house requires to hold one contract.

    Attributes:
        contract_symbol: ``futures_symbol`` of the contract this applies to.
        initial: Money required to open one contract.
        maintenance: Money that must remain to keep one contract open. At or
            below ``initial`` -- a maintenance requirement above the initial one
            would make every new position immediately deficient.
        currency: What both amounts are denominated in. Not assumed to be the
            contract's settlement currency: a clearing house may call margin in
            a different currency from the one the contract settles in, and
            reconciling the two is FX.
        as_of: When the exchange published these figures, in Unix seconds. Kept
            because a margin requirement is revised and a backtest that applies
            today's figure to last year's position has used a number that did
            not exist.

    Raises:
        FuturesInputError: If either amount is negative, maintenance exceeds
            initial, or the currency is unnamed.
    """

    contract_symbol: str
    initial: Decimal
    maintenance: Decimal
    currency: str
    as_of: float

    def __post_init__(self) -> None:
        if not self.contract_symbol.strip():
            raise FuturesInputError("A margin specification names the contract it applies to.")
        if not self.currency.strip():
            raise FuturesInputError(
                f"{self.contract_symbol}: margin is money and names its currency."
            )
        if self.initial < Decimal("0") or self.maintenance < Decimal("0"):
            raise FuturesInputError(
                f"{self.contract_symbol}: margin requirements are not negative "
                f"(initial={self.initial}, maintenance={self.maintenance})."
            )
        if self.maintenance > self.initial:
            raise FuturesInputError(
                f"{self.contract_symbol}: maintenance {self.maintenance} exceeds initial "
                f"{self.initial}, which would make every position deficient the moment it "
                "opened."
            )


@dataclass(frozen=True, slots=True)
class MarginPosture:
    """What a position requires, and against which published figures.

    Attributes:
        contract_symbol: The contract.
        contracts: How many, as an absolute count -- margin is required for a
            short exactly as for a long, so the sign of a position does not
            reduce it.
        initial: ``contracts * spec.initial``.
        maintenance: ``contracts * spec.maintenance``.
        currency: What both figures are in.
        as_of: The publication instant of the figures used.
    """

    contract_symbol: str
    contracts: Decimal
    initial: Decimal
    maintenance: Decimal
    currency: str
    as_of: float


def position_margin(
    contract: FutureContract,
    quantity: Decimal,
    specifications: Mapping[str, ContractMarginSpec],
    as_of: float,
) -> MarginPosture:
    """What holding ``quantity`` contracts requires.

    Args:
        contract: The contract held.
        quantity: Signed contract count. Its magnitude is what is margined.
        specifications: Published requirements, keyed by ``futures_symbol``.
        as_of: The research instant. A specification published *after* this is
            refused rather than used: applying a requirement that did not exist
            yet is the same look-ahead as pricing with tomorrow's close.

    Raises:
        FuturesInputError: If no specification covers the contract, or the only
            one is not yet published at ``as_of``.
    """

    symbol = futures_symbol(contract)
    spec = specifications.get(symbol)
    if spec is None:
        raise FuturesInputError(
            f"No margin requirement was supplied for {symbol}. A clearing house sets it per "
            "contract and revises it without notice, so AlphaLab holds none and derives "
            "none; supply the published figures."
        )
    if spec.as_of > as_of:
        raise FuturesInputError(
            f"The only margin requirement for {symbol} was published at {spec.as_of}, after "
            f"the research instant {as_of}. Using it would apply a figure that did not exist "
            "when the position was held."
        )
    contracts = abs(quantity)
    return MarginPosture(
        contract_symbol=symbol,
        contracts=contracts,
        initial=contracts * spec.initial,
        maintenance=contracts * spec.maintenance,
        currency=spec.currency,
        as_of=spec.as_of,
    )
