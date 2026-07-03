"""Immutable interface protocol for Feed implementations."""

from typing import Any, Protocol

from alphalab.feed.events import FeedEvent
from alphalab.feed.state import FeedState


class FeedProtocol(Protocol):
    """Pure functional interface defining the contract for data providers."""

    def connect(
        self, state: FeedState, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]: ...

    def disconnect(
        self, state: FeedState, reason: str, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]: ...

    def subscribe(
        self, state: FeedState, symbol: str, feed_type: str, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]: ...

    def unsubscribe(
        self, state: FeedState, symbol: str, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]: ...

    def heartbeat(
        self, state: FeedState, latency_ms: float, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]: ...

    def publish(
        self, state: FeedState, payload: Any, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]: ...

    def status(self, state: FeedState) -> bool: ...
