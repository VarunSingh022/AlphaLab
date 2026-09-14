"""Immutable Capital Budget models.

A budget is a quantity of money, so it is in a currency, and v2.17 is where it
says so. ADR-0033 decision 13 named this as the third of four blockers to
settlement-level multi-currency, in its own words: "Allocation sizes against a
capital budget in one currency". While a pipeline could settle only one currency
that sentence was harmless -- the budget's currency was determined by the only
one in play. The moment a pipeline can settle two, an unstated budget currency
means every sizing decision is made against a figure nobody can price.

``currency`` is therefore ``""`` by default, and ``""`` means **unstated**, not
``"USD"``. The distinction is the one ADR-0033 decision 8 draws for
``actor_id``: an empty reference is the honest answer when there is genuinely
nothing to name, and it is checked where it matters rather than filled in where
it does not.

:func:`~alphalab.runtime.execution_pipeline._require_settleable_budget` is that
check. A single-currency pipeline accepts an unstated budget, because the
currency is determined; a **multi-currency** pipeline refuses one, because it is
not. Either way a budget that names a currency the pipeline does not settle is
refused, which catches the transposition a default would have hidden.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CapitalBudget:
    """Immutable representation of available capital and exposure limits.

    Attributes:
        global_capital: Total capital this budget may deploy.
        maximum_exposure: Ceiling on committed notional.
        cash_buffer: Capital held back from ``global_capital``.
        strategy_budgets: Per-strategy ceilings, keyed by ``strategy_id``.
        currency: What every amount above is denominated in. ``""`` means
            **unstated**, which a single-currency pipeline determines and a
            multi-currency one refuses. See the module docstring.
    """

    global_capital: Decimal
    maximum_exposure: Decimal
    cash_buffer: Decimal = Decimal("0.00")
    strategy_budgets: Mapping[str, Decimal] = field(default_factory=dict)
    currency: str = ""

    @property
    def available_global_capital(self) -> Decimal:
        """Total capital allowed to be deployed after cash buffer."""
        return max(Decimal("0.00"), self.global_capital - self.cash_buffer)

    def available_strategy_capital(self, strategy_id: str) -> Decimal:
        """Capital available for a specific strategy."""
        return self.strategy_budgets.get(strategy_id, self.available_global_capital)

    @property
    def states_currency(self) -> bool:
        """Whether this budget says what its amounts are denominated in."""

        return bool(self.currency.strip())

    def in_currency(self, currency: str) -> "CapitalBudget":
        """The same budget, stated in ``currency``.

        A **relabelling, not a conversion**: every amount is unchanged. It
        exists so a caller can declare a budget's currency without rebuilding
        it, and it is deliberately not a rate-applying helper -- converting a
        budget would need a rate, and a budget converted behind a caller's back
        is exactly the invented figure ADR-0020 refuses.
        """

        return CapitalBudget(
            global_capital=self.global_capital,
            maximum_exposure=self.maximum_exposure,
            cash_buffer=self.cash_buffer,
            strategy_budgets=self.strategy_budgets,
            currency=currency,
        )
