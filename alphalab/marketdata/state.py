"""Global immutable state container for Market Data Engine."""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
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
    """Deterministic snapshot of every registered provider and what it has sent.

    The indexes are :class:`~alphalab.common.persistent_map.PersistentMap` and
    ``events`` is an :class:`~alphalab.common.append_log.AppendOnlyLog`. As
    ``dict`` and ``tuple`` they were rebuilt on every message, so ingesting N
    ticks copied O(N^2) events -- the same quadratic v2.1 removed from the risk
    engine. It is the reason ``benchmarks/benchmark_marketdata.py`` could not
    finish its 100k-tick workload.
    """

    engine_id: str
    providers: PersistentMap[str, ProviderConfig] = field(default_factory=PersistentMap)
    connections: PersistentMap[str, ConnectionState] = field(default_factory=PersistentMap)
    subscriptions: PersistentMap[str, Subscription] = field(default_factory=PersistentMap)
    cache: MarketDataCache = field(default_factory=MarketDataCache)
    metrics: PersistentMap[str, ProviderMetrics] = field(default_factory=PersistentMap)
    metadata: PersistentMap[str, MarketMetadata] = field(default_factory=PersistentMap)
    quotes: PersistentMap[str, Quote] = field(default_factory=PersistentMap)
    trades: PersistentMap[str, Trade] = field(default_factory=PersistentMap)
    bars: PersistentMap[str, Bar] = field(default_factory=PersistentMap)
    order_books: PersistentMap[str, OrderBook] = field(default_factory=PersistentMap)
    events: AppendOnlyLog[MarketDataEvent] = field(default_factory=AppendOnlyLog)
