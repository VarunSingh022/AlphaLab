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
    ) -> list[tuple[str, str, Decimal]]:
        """Applies the sizing model to derive raw deltas per intent.

        Returns ``(strategy_id, instrument, quantity)`` triples. Before v2.6 the
        strategy was dropped here -- one statement before netting -- which is
        where every downstream attribution number lost its subject. Keeping it
        costs one tuple element and is what lets a netted order say which
        strategies asked for it. See ADR-0015 decision 4.
        """
        sized: list[tuple[str, str, Decimal]] = []
        for intent in intents:
            price = market_prices.get(intent.instrument, Decimal("0.00"))
            qty = sizing_model.calculate(intent, budget, price)
            sized.append((intent.strategy_id, intent.instrument, qty))
        return sized
