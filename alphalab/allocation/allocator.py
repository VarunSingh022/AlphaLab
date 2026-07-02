"""Core aggregation mapping logic translating Intents to Quantities."""

from collections.abc import Mapping
from decimal import Decimal

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.sizing import SizingModel
from alphalab.strategy.events import Intent


class IntentAllocator:
    """Stateless translator mapping Intents to actionable Deltas."""

    @staticmethod
    def size_intents(
        intents: tuple[Intent, ...],
        budget: CapitalBudget,
        market_prices: Mapping[str, Decimal],
        sizing_model: SizingModel,
    ) -> list[tuple[str, Decimal]]:
        """Applies the sizing model to derive raw deltas per intent."""
        sized: list[tuple[str, Decimal]] = []
        for intent in intents:
            price = market_prices.get(intent.instrument, Decimal("0.00"))
            qty = sizing_model.calculate(intent, budget, price)
            sized.append((intent.instrument, qty))
        return sized
