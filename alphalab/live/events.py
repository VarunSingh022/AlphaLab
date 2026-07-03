"""Immutable domain events describing the Live Market Data lifecycle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LiveEvent:
    """Base class for all Live Market Data events."""

    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class ProviderRegistered(LiveEvent):
    provider_id: str
    vendor: str


@dataclass(frozen=True, slots=True)
class ProviderConnected(LiveEvent):
    provider_id: str


@dataclass(frozen=True, slots=True)
class ProviderDisconnected(LiveEvent):
    provider_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class SubscriptionCreated(LiveEvent):
    provider_id: str
    symbol: str
    asset_class: str


@dataclass(frozen=True, slots=True)
class SubscriptionRemoved(LiveEvent):
    provider_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class TickReceived(LiveEvent):
    provider_id: str
    symbol: str
    tick_type: str


@dataclass(frozen=True, slots=True)
class SnapshotUpdated(LiveEvent):
    symbol: str
    last_price: float
