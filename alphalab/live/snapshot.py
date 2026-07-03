"""Immutable models tracking the latest top-of-book and trade states."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Immutable aggregation of the latest market data for a symbol."""

    symbol: str
    timestamp: float
    last_trade_price: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0
    volume: float = 0.0
