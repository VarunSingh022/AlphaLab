"""Normalized immutable message payloads from external providers."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketMessage:
    """Base class for all normalized market data messages."""

    provider_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class TradeTick(MarketMessage):
    """Normalized execution print."""

    symbol: str
    price: float
    size: float


@dataclass(frozen=True, slots=True)
class QuoteTick(MarketMessage):
    """Normalized top-of-book update."""

    symbol: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot(MarketMessage):
    """Normalized market depth snapshot."""

    symbol: str
    bids: Sequence[OrderBookLevel]
    asks: Sequence[OrderBookLevel]


@dataclass(frozen=True, slots=True)
class MarketStatus(MarketMessage):
    """Normalized exchange status (e.g., HALT, OPEN, CLOSE)."""

    symbol: str
    status: str


@dataclass(frozen=True, slots=True)
class Heartbeat(MarketMessage):
    """Provider keep-alive ping."""

    latency_ms: float
