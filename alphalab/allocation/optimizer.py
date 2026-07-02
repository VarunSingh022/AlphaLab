"""Portfolio optimization interface."""

from decimal import Decimal
from typing import Protocol

from alphalab.allocation.constraints import AllocationConstraints
from alphalab.strategy.events import Intent


class AllocationOptimizer(Protocol):
    """Protocol for advanced allocation optimizers (e.g. Mean-Variance)."""

    def optimize(
        self, intents: tuple[Intent, ...], constraints: AllocationConstraints
    ) -> dict[str, Decimal]:
        """Returns optimized target weights per asset."""
        ...
