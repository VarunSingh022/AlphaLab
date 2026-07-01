"""Immutable Top of Book (BBO) quote model."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Quote:
    """Immutable Top of Book Quote containing best bid and best ask."""

    asset_id: str
    timestamp: float
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    venue: str
    currency: str
