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

Per-strategy ceilings
---------------------
``strategy_budgets`` has always been read by the sizing models: a strategy's
weight is a share of *its* capital. It was never a limit -- until v3.12 nothing
stopped a strategy deploying more than its budget, event after event, so long as
the account's total fitted (ledger OFE-003). ``enforce_strategy_budgets=True``
makes each declared amount a **ceiling on the capital the strategy commits**:
what its positions cost plus what its working orders reserve, kept per strategy
by :class:`~alphalab.allocation.ceilings.StrategyCapital`. An intent that would
take a strategy over its ceiling is refused, and the reason recorded; a
strategy with no declared amount has no ceiling but the account's. ``False``,
the default, is every run before v3.12 and changes nothing.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

from alphalab.allocation.exceptions import AllocationValidationError


@dataclass(frozen=True, slots=True)
class CapitalBudget:
    """Immutable representation of available capital and exposure limits.

    Attributes:
        global_capital: Total capital this budget may deploy.
        maximum_exposure: Ceiling on committed notional.
        cash_buffer: Capital held back from ``global_capital``.
        strategy_budgets: Each strategy's capital, keyed by ``strategy_id``:
            what a sizing model sizes its intents against, and -- when
            ``enforce_strategy_budgets`` -- the ceiling on what it may commit.
        currency: What every amount above is denominated in. ``""`` means
            **unstated**, which a single-currency pipeline determines and a
            multi-currency one refuses. See the module docstring.
        enforce_strategy_budgets: Whether each amount in ``strategy_budgets``
            is a ceiling on the execution path (ledger OFE-003). See the module
            docstring.

    Raises:
        AllocationValidationError: If ceilings are enforced and one is not a
            finite, non-negative amount.
    """

    global_capital: Decimal
    maximum_exposure: Decimal
    cash_buffer: Decimal = Decimal("0.00")
    strategy_budgets: Mapping[str, Decimal] = field(default_factory=dict)
    currency: str = ""
    enforce_strategy_budgets: bool = False

    def __post_init__(self) -> None:
        if not self.enforce_strategy_budgets:
            return
        for strategy_id, ceiling in self.strategy_budgets.items():
            if not ceiling.is_finite() or ceiling < 0:
                raise AllocationValidationError(
                    f"{strategy_id}'s budget is enforced as a ceiling and must be a finite, "
                    f"non-negative amount, got {ceiling}."
                )

    def strategy_ceiling(self, strategy_id: str) -> Decimal | None:
        """The most ``strategy_id`` may commit, or ``None`` when it has no ceiling of its own.

        ``None`` whenever ceilings are not enforced, and for a strategy the
        budget declares no amount for.
        """

        if not self.enforce_strategy_budgets:
            return None
        return self.strategy_budgets.get(strategy_id)

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

        return replace(self, currency=currency)
