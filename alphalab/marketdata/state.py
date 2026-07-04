"""Global immutable state container for Market Data Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.marketdata.cache import MarketDataCache
from alphalab.marketdata.config import ProviderConfig
from alphalab.marketdata.connection import ConnectionState
from alphalab.marketdata.events import MarketDataEvent
from alphalab.marketdata.feed import Bar, OrderBook, Quote, Trade
from alphalab.marketdata.metadata import MarketMetadata
from alphalab.marketdata.subscription import Subscription


@dataclass(frozen=True, slots=True)
class ProviderMetrics:
    requests: int = 0
    latency_ms: float = 0.0
    reconnects: int = 0
    failures: int = 0
    bytes_received: int = 0


@dataclass(frozen=True, slots=True)
class MarketDataHealth:
    is_healthy: bool
    active_providers: int
    failed_providers: int


@dataclass(frozen=True, slots=True)
class MarketDataState:
    engine_id: str
    providers: Mapping[str, ProviderConfig] = field(default_factory=dict)
    connections: Mapping[str, ConnectionState] = field(default_factory=dict)
    subscriptions: Mapping[str, Subscription] = field(default_factory=dict)
    cache: MarketDataCache = field(default_factory=MarketDataCache)
    metrics: Mapping[str, ProviderMetrics] = field(default_factory=dict)
    metadata: Mapping[str, MarketMetadata] = field(default_factory=dict)
    quotes: Mapping[str, Quote] = field(default_factory=dict)
    trades: Mapping[str, Trade] = field(default_factory=dict)
    bars: Mapping[str, Bar] = field(default_factory=dict)
    order_books: Mapping[str, OrderBook] = field(default_factory=dict)
    events: tuple[MarketDataEvent, ...] = field(default_factory=tuple)
