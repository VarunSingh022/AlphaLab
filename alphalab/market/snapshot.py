"""Immutable depth-of-book snapshot model."""

from dataclasses import dataclass

from alphalab.market.level import OrderBookLevel


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """Immutable representation of a full L2/L3 order book snapshot."""

    asset_id: str
    timestamp: float
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    sequence: int
