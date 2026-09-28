"""Immutable OHLCV Bar models."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Final


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


#: Seconds in one interval of each timeframe that has a fixed length. ``MN1``
#: has none -- a month is 28 to 31 days -- and a single figure for it would be
#: an invented average.
TIMEFRAME_SECONDS: Final[Mapping[TimeFrame, float]] = MappingProxyType(
    {
        TimeFrame.M1: 60.0,
        TimeFrame.M5: 300.0,
        TimeFrame.M15: 900.0,
        TimeFrame.H1: 3600.0,
        TimeFrame.H4: 14400.0,
        TimeFrame.D1: 86400.0,
        TimeFrame.W1: 604800.0,
    }
)


@dataclass(frozen=True, slots=True)
class Bar:
    """Immutable representation of an OHLCV candlestick bar.

    ``timestamp`` is the **end** of the bar's interval -- the instant its close
    became knowable, and the instant the run treats it as observed. A source
    that stamps bars at the start of their interval declares so, and ingestion
    and normalization move them (ledger DAT-001).
    """

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
