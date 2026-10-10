"""Core aggregation mapping logic translating Intents to Quantities."""

from collections.abc import Mapping
from decimal import Decimal

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.sizing import SizingModel
from alphalab.strategy.events import Intent

#: The price a sizing model is given for an instrument the run holds no price
#: for. A model that needs a price refuses it (see ``SizingModel``).
UNPRICED = Decimal("0")


class IntentAllocator:
    """Stateless translator mapping Intents to actionable Deltas."""

    @staticmethod
    def size_intent(
        intent: Intent,
        budget: CapitalBudget,
        market_prices: Mapping[str, Decimal],
        sizing_model: SizingModel,
    ) -> Decimal:
        """The signed delta ``sizing_model`` sizes for one intent.

        Raises:
            SizingRefusedError: If the model cannot size it -- the instrument has
                no positive price and the model needs one, say.
        """
        price = market_prices.get(intent.instrument, UNPRICED)
        return sizing_model.calculate(intent, budget, price)

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

        Raises:
            SizingRefusedError: If the model cannot size one of them.
                :meth:`~alphalab.allocation.engine.AllocationEngine.allocate`
                sizes one intent at a time instead, and records a refusal.
        """
        return [
            (
                intent.strategy_id,
                intent.instrument,
                IntentAllocator.size_intent(intent, budget, market_prices, sizing_model),
            )
            for intent in intents
        ]
