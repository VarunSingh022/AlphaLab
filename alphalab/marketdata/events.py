"""Immutable domain events describing the Market Data lifecycle."""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class MarketDataEvent(BaseEvent):
    pass


@dataclass(frozen=True, slots=True)
class ProviderRegistered(MarketDataEvent):
    provider_id: str


@dataclass(frozen=True, slots=True)
class ProviderConnected(MarketDataEvent):
    provider_id: str


@dataclass(frozen=True, slots=True)
class ProviderDisconnected(MarketDataEvent):
    provider_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProviderRecovered(MarketDataEvent):
    provider_id: str


@dataclass(frozen=True, slots=True)
class SubscriptionCreated(MarketDataEvent):
    subscription_id: str
    provider_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class SubscriptionRemoved(MarketDataEvent):
    subscription_id: str


@dataclass(frozen=True, slots=True)
class QuoteReceived(MarketDataEvent):
    provider_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class TradeReceived(MarketDataEvent):
    provider_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class BarReceived(MarketDataEvent):
    provider_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class OrderBookUpdated(MarketDataEvent):
    provider_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class HeartbeatReceived(MarketDataEvent):
    provider_id: str
    latency_ms: float
