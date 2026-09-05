"""Deterministic historical data caching."""

from dataclasses import dataclass, field

from alphalab.common.persistent_map import PersistentMap
from alphalab.marketdata.feed import Bar


@dataclass(frozen=True, slots=True)
class CacheRecord:
    symbol: str
    provider_id: str
    history: tuple[Bar, ...]


@dataclass(frozen=True, slots=True)
class MarketDataCache:
    """Immutable cache of historical lookups."""

    records: PersistentMap[str, CacheRecord] = field(default_factory=PersistentMap)
