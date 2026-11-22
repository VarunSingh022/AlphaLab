"""What a rebalancing strategy's weights are fractions of (v4.3, ADR-0063 section 6).

A construction answers in weights; a target is a quantity. The capital a
weight is a fraction of is stated, never assumed (AC-12):

* :class:`FixedCapital` -- a constant amount: a mandate's size, or the budget
  the run's allocation gives the strategy;
* :class:`AccountEquity` -- the account's cash in a currency and its positions
  in the strategy's assets, marked at the slice: capital that grows and
  shrinks with the strategy, for a strategy that trades the account alone.

A researcher's own rule satisfies :class:`CapitalRule`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from alphalab.rebalancing.exceptions import RebalancingInputError
from alphalab.strategy.context import StrategyContext

__all__ = ["AccountEquity", "CapitalRule", "FixedCapital"]


@runtime_checkable
class CapitalRule(Protocol):
    """The amount, in the prices' currency, a rebalancing strategy's weights are fractions of."""

    def rendering(self) -> list[str]:
        """The rule's identity lines."""
        ...

    def amount(
        self,
        context: StrategyContext,
        prices: Mapping[str, Decimal],
        multipliers: Mapping[str, Decimal],
    ) -> Decimal | None:
        """The capital at this slice, or ``None`` when it cannot be stated now.

        ``prices`` and ``multipliers`` cover every asset the strategy priced at
        the slice. ``None`` -- or an amount that is not positive -- means the
        strategy does not rebalance at this slice.
        """
        ...


@dataclass(frozen=True, slots=True)
class FixedCapital:
    """A constant capital: what every weight is a fraction of, at every slice.

    Attributes:
        capital: A positive, finite ``Decimal``, in the currency the strategy's
            assets are priced in.
    """

    capital: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.capital, Decimal) or not self.capital.is_finite():
            raise RebalancingInputError(f"capital must be a finite Decimal, got {self.capital!r}.")
        if self.capital <= 0:
            raise RebalancingInputError(f"capital is {self.capital}; it must be positive.")

    def rendering(self) -> list[str]:
        return ["capital=fixed", f"amount={self.capital}"]

    def amount(
        self,
        context: StrategyContext,
        prices: Mapping[str, Decimal],
        multipliers: Mapping[str, Decimal],
    ) -> Decimal | None:
        return self.capital


@dataclass(frozen=True, slots=True)
class AccountEquity:
    """The account's cash in ``currency`` plus its positions in the strategy's assets, marked.

    ``cash(currency) + sum_a quantity_a * price_a * multiplier_a`` over the
    assets priced at the slice: what the strategy is worth when it trades the
    account alone and every asset is priced in ``currency``, which the
    researcher states. Positions in other assets are not counted, and nothing
    is converted: a book in two currencies needs a rule of its own.

    Attributes:
        currency: The currency the cash is read in and the assets are priced in.
    """

    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise RebalancingInputError(f"currency must be named, got {self.currency!r}.")

    def rendering(self) -> list[str]:
        return ["capital=account_equity", f"currency={self.currency!r}"]

    def amount(
        self,
        context: StrategyContext,
        prices: Mapping[str, Decimal],
        multipliers: Mapping[str, Decimal],
    ) -> Decimal | None:
        total = context.portfolio.cash(self.currency)
        for asset in sorted(prices):
            quantity = context.portfolio.quantity(asset)
            if quantity:
                total += quantity * prices[asset] * multipliers[asset]
        return total if total > 0 else None
