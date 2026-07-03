"""Immutable domain events describing Feed lifecycle and data reception."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FeedEvent:
    """Base class for all Feed lifecycle and data events."""
    event_id: str
    timestamp: float
    provider_id: str


@dataclass(frozen=True, slots=True)
class FeedConnected(FeedEvent):
    """Emitted when the feed adapter establishes a connection."""
    pass


@dataclass(frozen=True, slots=True)
class FeedDisconnected(FeedEvent):
    """Emitted when the feed adapter loses connection."""
    reason: str


@dataclass(frozen=True, slots=True)
class FeedSubscribed(FeedEvent):
    """Emitted when a symbol subscription becomes active."""
    symbol: str
    feed_type: str


@dataclass(frozen=True, slots=True)
class FeedUnsubscribed(FeedEvent):
    """Emitted when a symbol subscription is cancelled."""
    symbol: str


@dataclass(frozen=True, slots=True)
class MarketDataReceived(FeedEvent):
    """Emitted when normalized market data is ready for the engine."""
    payload: Any


@dataclass(frozen=True, slots=True)
class HeartbeatReceived(FeedEvent):
    """Emitted when a keep-alive is received from the provider."""
    latency_ms: float