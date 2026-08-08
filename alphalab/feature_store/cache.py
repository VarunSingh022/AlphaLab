"""Deterministic immutable caching of the latest feature values.

Consistent with the rest of AlphaLab's immutable architecture, the cache is not a
mutable structure with side effects -- it is a plain immutable snapshot, and every
operation returns a new `FeatureCache` rather than mutating one in place. This
mirrors `alphalab.marketdata.cache.MarketDataCache`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from alphalab.feature_store.value import FeatureValue


def cache_key(feature_id: str, version: int, asset_id: str | None) -> str:
    """Builds the canonical cache/store key for a feature value."""
    scope = asset_id if asset_id is not None else "_GLOBAL"
    return f"{feature_id}:{version}:{scope}"


@dataclass(frozen=True, slots=True)
class FeatureCache:
    """Immutable read-through cache of the most recently written feature values."""

    entries: Mapping[str, FeatureValue] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0


def cache_value(cache: FeatureCache, value: FeatureValue) -> FeatureCache:
    """Returns a new cache with the given value stored under its canonical key."""
    key = cache_key(value.feature_id, value.version, value.asset_id)
    new_entries = dict(cache.entries)
    new_entries[key] = value
    return replace(cache, entries=new_entries)


def cached_value(
    cache: FeatureCache, feature_id: str, version: int, asset_id: str | None
) -> tuple[FeatureCache, FeatureValue | None]:
    """Looks up a cached value, returning an updated cache with hit/miss tracked."""
    key = cache_key(feature_id, version, asset_id)
    found = cache.entries.get(key)
    if found is not None:
        return replace(cache, hits=cache.hits + 1), found
    return replace(cache, misses=cache.misses + 1), None


def invalidate(
    cache: FeatureCache, feature_id: str, version: int, asset_id: str | None
) -> FeatureCache:
    """Returns a new cache with the given entry removed, if present."""
    key = cache_key(feature_id, version, asset_id)
    if key not in cache.entries:
        return cache
    new_entries = dict(cache.entries)
    del new_entries[key]
    return replace(cache, entries=new_entries)


def clear(cache: FeatureCache) -> FeatureCache:
    """Returns a new, fully empty cache, preserving no hit/miss history."""
    return FeatureCache()
