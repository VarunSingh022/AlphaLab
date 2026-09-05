"""Normalized immutable message payloads from external providers.

These are *provider-tagged* wire messages: every one carries the ``provider_id``
that produced it, which is what distinguishes them from the untagged wire records
in :mod:`alphalab.data.feed`. The live layer routes and validates by provider, so
the tag is load-bearing here and the shapes stay separate.

``OrderBookLevel`` is the exception: it carried no provider tag and was
field-for-field identical to :class:`alphalab.data.feed.OrderBookLevel`, so as of
v2.3 it is that class, re-exported. See :mod:`alphalab.market.normalization` for
the boundary that lifts any of these into the canonical domain records the
execution path consumes.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from alphalab.data.feed import OrderBookLevel

__all__ = [
    "Heartbeat",
    "MarketMessage",
    "MarketStatus",
    "OrderBookLevel",
    "OrderBookSnapshot",
    "QuoteTick",
    "TradeTick",
]


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
