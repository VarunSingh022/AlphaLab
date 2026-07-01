"""Immutable market events."""

from dataclasses import dataclass

from alphalab.market.bar import Bar
from alphalab.market.quote import Quote
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.tick import Tick


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class TickReceived(MarketEvent):
    tick: Tick


@dataclass(frozen=True, slots=True)
class TradeReceived(MarketEvent):
    tick: Tick


@dataclass(frozen=True, slots=True)
class QuoteReceived(MarketEvent):
    quote: Quote


@dataclass(frozen=True, slots=True)
class BarClosed(MarketEvent):
    bar: Bar


@dataclass(frozen=True, slots=True)
class BookUpdated(MarketEvent):
    snapshot: OrderBookSnapshot


@dataclass(frozen=True, slots=True)
class SnapshotCreated(MarketEvent):
    snapshot: OrderBookSnapshot
