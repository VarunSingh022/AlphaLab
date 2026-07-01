"""Immutable OHLCV Bar models."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class TimeFrame(Enum):
    """Supported intervals for OHLCV aggregation."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    MN1 = "1M"


@dataclass(frozen=True, slots=True)
class Bar:
    """Immutable representation of an OHLCV candlestick bar."""

    asset_id: str
    timestamp: float
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal
    trade_count: int
    timeframe: TimeFrame
