"""Money accumulated per currency, and the one place it is summed into one.

``PortfolioState.realized_pnl`` and ``PortfolioState.commission_paid`` were
single cumulative ``Decimal`` scalars naming no currency. ADR-0033 decision 13
named that as the first two of four blockers standing between AlphaLab and
settlement-level multi-currency, in its own words:

    ``PortfolioState.realized_pnl`` and ``commission_paid`` are single cumulative
    scalars that name no currency, and a run trading in two would sum them
    across both.

That is what this type removes. A :class:`CurrencyAmounts` is a mapping from
currency to an exact amount, so a fill booked in JPY accrues against JPY and a
fill booked in USD against USD, and no addition is ever performed across two.

Why this is not :class:`~alphalab.portfolio.cash.CashLedger`
------------------------------------------------------------

They look alike -- both are ``currency -> Decimal`` -- and they are different
values with different invariants, which is why merging them would be a category
error rather than a simplification:

============================ ====================================================
``CashLedger``               Settled money. Has *reservations*, refuses an
                             overdraft through
                             :class:`~alphalab.portfolio.exceptions.InsufficientFundsError`,
                             and answers "what can I spend?".
``CurrencyAmounts``          An accounting *result*. Has no reservations, refuses
                             nothing, and is freely negative -- realized P&L is
                             negative on a losing trade and that is not an error.
============================ ====================================================

Reporting, and where the conversion happens
--------------------------------------------

This type is **settlement truth**: what was actually realized, in the currency it
was actually realized in. It is deliberately not a reporting figure.
:meth:`total_in` is the one place a per-currency accumulation becomes a single
number, it takes an explicit :class:`~alphalab.portfolio.fx.FxRates`, and it
returns the conversions it performed alongside the total -- so a converted figure
stays attributable, which is the property ADR-0020 removed a number for lacking.

That split is the whole of the task ADR-0033 left open: **trade and settlement
currency stay separate from reporting and valuation currency**, and the rate that
joins them is stated rather than assumed.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.fx import NO_RATES, FxConversion, FxRates
from alphalab.portfolio.money import ZERO_MONEY, to_money

__all__ = ["CurrencyAmounts"]


@dataclass(frozen=True, slots=True)
class CurrencyAmounts(Mapping[str, Decimal]):
    """An immutable per-currency accumulation of exact monetary amounts.

    A ``Mapping``, so every existing read -- ``amounts["USD"]``, ``len``,
    iteration, ``dict(amounts)`` -- works without a helper. Every mutation
    returns a new value, like every other state in AlphaLab.

    Attributes:
        amounts: Currency -> total. Only currencies that have actually accrued
            appear; a currency that has never been booked is absent rather than
            zero, and :meth:`of` returns ``0.00`` for it. That distinction is
            the same one :func:`~alphalab.portfolio.valuation.foreign_currencies`
            draws for a spent cash balance.
    """

    amounts: Mapping[str, Decimal] = field(default_factory=dict)

    # -- Mapping ----------------------------------------------------------- #

    def __getitem__(self, currency: str) -> Decimal:
        return self.amounts[currency]

    def __iter__(self) -> Iterator[str]:
        return iter(self.amounts)

    def __len__(self) -> int:
        return len(self.amounts)

    # -- Value operations --------------------------------------------------- #

    @classmethod
    def single(cls, amount: Decimal, currency: str) -> CurrencyAmounts:
        """One currency, one amount. The usual way to build a non-empty value."""

        return cls({currency: to_money(amount)})

    def of(self, currency: str) -> Decimal:
        """The total accrued in ``currency``, or ``0.00`` if none has."""

        return self.amounts.get(currency, ZERO_MONEY)

    def add(self, amount: Decimal, currency: str) -> CurrencyAmounts:
        """A new value with ``amount`` added to ``currency``.

        ``amount`` is rounded to the currency's minor unit exactly once, here,
        following :mod:`alphalab.portfolio.money` rule 2. Callers that have
        already rounded -- which every fill path has, because a fill's notional
        and commission are rounded at entry -- pay nothing for it, because
        rounding an exact value is the identity.
        """

        if not currency.strip():
            raise PortfolioError(
                "An amount must name the currency it is in. A currency-less "
                "accumulation is exactly what ADR-0033 decision 13 named as the "
                "blocker to settlement-level multi-currency."
            )
        return CurrencyAmounts({**self.amounts, currency: self.of(currency) + to_money(amount)})

    @property
    def currencies(self) -> tuple[str, ...]:
        """Every currency this value has accrued in, sorted."""

        return tuple(sorted(self.amounts))

    @property
    def is_single_currency(self) -> bool:
        """Whether at most one currency has accrued.

        ``True`` for an empty value: a book that has realized nothing is not a
        mixed book, and treating it as one would make every fresh portfolio
        need a rate.
        """

        return len(self.amounts) <= 1

    def total_in(
        self, currency: str, rates: FxRates = NO_RATES, as_of: float | None = None
    ) -> tuple[Decimal, tuple[FxConversion, ...]]:
        """Express the whole accumulation in ``currency``, and say how.

        The **one** place a per-currency accumulation becomes a single figure.
        Returns the total *and* every conversion performed, so a caller that
        reports the number can also report the rates behind it.

        A zero amount in another currency is skipped: it converts to zero, and
        recording a rate for it would put noise in the provenance -- the same
        rule :func:`~alphalab.portfolio.valuation.cash_in` follows.

        Raises:
            MixedCurrencyValuationError: If another currency has accrued and no
                rates at all were supplied. The message says which currencies,
                because "cannot value" is not actionable and "no rate for JPY"
                is.
            MissingRateError: If rates were supplied and this pair is not among
                them.
            StaleRateError: If the only rate for a pair is older than the table
                tolerates at ``as_of``.
        """

        from alphalab.portfolio.exceptions import MixedCurrencyValuationError

        total = ZERO_MONEY
        performed: list[FxConversion] = []
        for held, amount in self.amounts.items():
            if held == currency:
                total += amount
                continue
            if amount == ZERO_MONEY:
                continue
            if not rates:
                raise MixedCurrencyValuationError(
                    f"This accumulation holds {list(self.currencies)} and cannot be "
                    f"expressed as one figure in {currency!r}: no FX rates were "
                    "supplied, so there is no honest single number to return. Pass an "
                    "alphalab.portfolio.fx.FxRates table covering "
                    f"{[c for c in self.currencies if c != currency]}, or read each "
                    "currency separately with of()."
                )
            conversion = rates.convert(amount, held, currency, as_of)
            total += conversion.converted
            performed.append(conversion)
        return total, tuple(performed)

    def __repr__(self) -> str:
        rendered = ", ".join(f"{c}={self.amounts[c]}" for c in self.currencies)
        return f"CurrencyAmounts({rendered})"

    def __serializable__(self) -> dict[str, Decimal]:
        """Serialize as the mapping it stands for, in sorted-key order."""

        return {currency: self.amounts[currency] for currency in self.currencies}
