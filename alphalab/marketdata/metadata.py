"""Canonical Market Metadata and Status objects."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketMetadata:
    exchange: str
    timezone: str
    currency: str
    trading_hours: str

@dataclass(frozen=True, slots=True)
class MarketStatus:
    symbol: str
    status: str
    timestamp: float