"""AlphaLab Canonical Market Data Objects."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    symbol: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class Quote(CanonicalRecord):
    bid: float
    ask: float
    bid_size: float
    ask_size: float


@dataclass(frozen=True, slots=True)
class Trade(CanonicalRecord):
    price: float
    size: float


@dataclass(frozen=True, slots=True)
class Bar(CanonicalRecord):
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(frozen=True, slots=True)
class OrderBook(CanonicalRecord):
    bids: tuple[OrderBookLevel, ...] = field(default_factory=tuple)
    asks: tuple[OrderBookLevel, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CorporateAction(CanonicalRecord):
    action_type: str
    details: str


@dataclass(frozen=True, slots=True)
class Dividend(CanonicalRecord):
    amount: float
    currency: str


@dataclass(frozen=True, slots=True)
class Split(CanonicalRecord):
    ratio: float


@dataclass(frozen=True, slots=True)
class FundamentalRecord(CanonicalRecord):
    metric_name: str
    metric_value: float


@dataclass(frozen=True, slots=True)
class EconomicEvent(CanonicalRecord):
    event_name: str
    actual: float
    forecast: float
    previous: float


@dataclass(frozen=True, slots=True)
class AlternativeDataRecord(CanonicalRecord):
    source: str
    sentiment_score: float
