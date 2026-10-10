"""Canonical *wire* records: the transport shape market data arrives in.

AlphaLab has two market-data layers, and they are deliberately different types:

* **Wire records** (this module) -- ``float`` prices and sizes keyed by a
  provider ``symbol``. This is the shape data has when it comes off a file, a
  REST response or a socket, before anything has decided what venue, currency or
  precision it belongs to. It is intentionally lossy and cheap: a provider can
  fill one in without knowing anything about AlphaLab's domain.
* **Canonical domain records** (:mod:`alphalab.market`) -- ``Decimal`` prices and
  sizes keyed by ``asset_id``, carrying venue, currency and, for bars, timeframe
  and trade count. This is what the execution path consumes and what
  :mod:`alphalab.market.normalization` produces from the records defined here.

Keeping both is not duplication: they answer different questions. What *was*
duplication, before v2.3, is that ``alphalab.marketdata.feed`` defined a second,
field-for-field identical copy of every type below. That module now re-exports
these definitions, so there is exactly one wire record per concept.

Non-market records (corporate actions, fundamentals, economic releases,
alternative data) also live here because they share ``CanonicalRecord``'s
``symbol`` + ``timestamp`` identity and the same ingestion path.
"""

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "AlternativeDataRecord",
    "Bar",
    "CanonicalRecord",
    "CorporateAction",
    "Dividend",
    "EconomicEvent",
    "FundamentalRecord",
    "OrderBook",
    "OrderBookLevel",
    "Quote",
    "Split",
    "Trade",
    "TradeAggressor",
]


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    """Identity shared by every wire record: which instrument, and when."""

    symbol: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class Quote(CanonicalRecord):
    """Top-of-book bid/ask as reported by a provider."""

    bid: float
    ask: float
    bid_size: float
    ask_size: float


class TradeAggressor(StrEnum):
    """Which side of a print crossed the spread to make it, as the venue says.

    Only ever *reported*. A print's direction is not inferred here -- not by a
    tick rule, not by comparing it with a quote -- because every such rule is a
    model, and a model's guess stored in the field a venue's flag goes in would
    be indistinguishable from the flag.
    """

    #: A buyer took the offer.
    BUYER = "BUYER"

    #: A seller hit the bid.
    SELLER = "SELLER"


@dataclass(frozen=True, slots=True)
class Trade(CanonicalRecord):
    """A single execution print as reported by a provider.

    Attributes:
        price: The price the print went off at.
        size: How much traded.
        trade_id: The venue's identifier for the print, or ``""`` when the
            source carries none. Since v3.12 (ledger FEA-004): it is what tells
            a repeated row from two prints of the same size at the same instant.
        aggressor: Which side crossed the spread, when the source reports it;
            ``None`` when it does not. Since v3.12.
    """

    price: float
    size: float
    trade_id: str = ""
    aggressor: TradeAggressor | None = None


@dataclass(frozen=True, slots=True)
class Bar(CanonicalRecord):
    """OHLCV aggregate as reported by a provider.

    Carries no timeframe: the caller knows which timeframe it requested, and
    providers do not report it uniformly. :class:`alphalab.market.bar.Bar`, the
    canonical domain bar, does carry one -- normalization supplies it.
    """

    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    """One price level of a depth book, without order count."""

    price: float
    size: float


@dataclass(frozen=True, slots=True)
class OrderBook(CanonicalRecord):
    """A depth-of-book snapshot as reported by a provider."""

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
