"""Immutable Capital Budget models."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CapitalBudget:
    """Immutable representation of available capital and exposure limits."""

    global_capital: Decimal
    maximum_exposure: Decimal
    cash_buffer: Decimal = Decimal("0.00")
    strategy_budgets: Mapping[str, Decimal] = field(default_factory=dict)

    @property
    def available_global_capital(self) -> Decimal:
        """Total capital allowed to be deployed after cash buffer."""
        return max(Decimal("0.00"), self.global_capital - self.cash_buffer)

    def available_strategy_capital(self, strategy_id: str) -> Decimal:
        """Capital available for a specific strategy."""
        return self.strategy_budgets.get(strategy_id, self.available_global_capital)
