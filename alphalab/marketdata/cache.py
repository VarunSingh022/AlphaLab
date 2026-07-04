"""Deterministic historical data caching."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.marketdata.feed import Bar


@dataclass(frozen=True, slots=True)
class CacheRecord:
    symbol: str
    provider_id: str
    history: tuple[Bar, ...]


@dataclass(frozen=True, slots=True)
class MarketDataCache:
    """Immutable cache of historical lookups."""

    records: Mapping[str, CacheRecord] = field(default_factory=dict)
