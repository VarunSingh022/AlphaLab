"""Immutable interface protocol for generic Live Providers."""

from typing import Protocol

from alphalab.live.events import LiveEvent
from alphalab.live.state import LiveState


class ProviderProtocol(Protocol):
    """Pure functional interface defining standardized vendor integration."""

    def connect(
        self, state: LiveState, timestamp: float
    ) -> tuple[LiveState, tuple[LiveEvent, ...]]: ...

    def disconnect(
        self, state: LiveState, reason: str, timestamp: float
    ) -> tuple[LiveState, tuple[LiveEvent, ...]]: ...

    def subscribe(
        self, state: LiveState, symbol: str, timestamp: float
    ) -> tuple[LiveState, tuple[LiveEvent, ...]]: ...

    def unsubscribe(
        self, state: LiveState, symbol: str, timestamp: float
    ) -> tuple[LiveState, tuple[LiveEvent, ...]]: ...

    def heartbeat(
        self, state: LiveState, latency_ms: float, timestamp: float
    ) -> tuple[LiveState, tuple[LiveEvent, ...]]: ...
