"""Immutable market tick model."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.data.feed import TradeAggressor


@dataclass(frozen=True, slots=True)
class Tick:
    """Immutable representation of a single market trade (Tick).

    Attributes:
        aggressor: Which side crossed the spread, when the source reported it;
            ``None`` when it did not, which is not the same as either side.
            Since v3.12 (ledger FEA-004).
    """

    asset_id: str
    timestamp: float
    price: Decimal
    quantity: Decimal
    trade_id: str
    venue: str
    currency: str
    aggressor: TradeAggressor | None = None
