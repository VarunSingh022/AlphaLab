"""AlphaLab Market Data Engine: the canonical market-data domain.

The types here are what AlphaLab's execution path consumes: ``Decimal`` prices
and sizes keyed by ``asset_id``, carrying venue, currency, timeframe and
sequence. Provider wire records (``float``, provider ``symbol``) live in
:mod:`alphalab.data.feed`; :mod:`alphalab.market.normalization` is the boundary
between the two, and :mod:`alphalab.market.source` is where records come from.
"""

from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.book import best_ask, best_bid, mid_price, spread, weighted_mid
from alphalab.market.engine import MarketEngine
from alphalab.market.events import (
    BarClosed,
    BookUpdated,
    MarketEvent,
    QuoteReceived,
    SnapshotCreated,
    TickReceived,
    TradeReceived,
)
from alphalab.market.exceptions import (
    MarketDataError,
    MarketValidationError,
    UnsupportedRecordError,
)
from alphalab.market.level import OrderBookLevel
from alphalab.market.normalization import (
    DEFAULT_POLICY,
    NormalizationPolicy,
    SymbolMap,
    is_stale,
    normalize_wire_bar,
    normalize_wire_book,
    normalize_wire_quote,
    normalize_wire_trade,
    reject_stale,
    to_decimal,
)
from alphalab.market.quote import Quote
from alphalab.market.record import MarketInput, MarketRecord, records_from_inputs
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.source import (
    MarketDataSource,
    OrderingGuarantee,
    SequenceSource,
    validate_ordering,
)
from alphalab.market.state import MarketState
from alphalab.market.tick import Tick
from alphalab.market.timestamp import is_valid_timestamp, to_unix_milliseconds, to_unix_seconds
from alphalab.market.validation import (
    validate_bar,
    validate_quote,
    validate_snapshot,
    validate_tick,
)
from alphalab.market.views import (
    bars,
    books,
    latest_bar,
    latest_book,
    latest_quote,
    latest_tick,
    quotes,
    ticks,
)

__all__ = [
    "DEFAULT_POLICY",
    "Bar",
    "BarClosed",
    "BookUpdated",
    "MarketDataError",
    "MarketDataSource",
    "MarketEngine",
    "MarketEvent",
    "MarketInput",
    "MarketRecord",
    "MarketState",
    "MarketValidationError",
    "NormalizationPolicy",
    "OrderBookLevel",
    "OrderBookSnapshot",
    "OrderingGuarantee",
    "Quote",
    "QuoteReceived",
    "SequenceSource",
    "SnapshotCreated",
    "SymbolMap",
    "Tick",
    "TickReceived",
    "TimeFrame",
    "TradeReceived",
    "UnsupportedRecordError",
    "bars",
    "best_ask",
    "best_bid",
    "books",
    "is_stale",
    "is_valid_timestamp",
    "latest_bar",
    "latest_book",
    "latest_quote",
    "latest_tick",
    "mid_price",
    "normalize_wire_bar",
    "normalize_wire_book",
    "normalize_wire_quote",
    "normalize_wire_trade",
    "quotes",
    "records_from_inputs",
    "reject_stale",
    "spread",
    "ticks",
    "to_decimal",
    "to_unix_milliseconds",
    "to_unix_seconds",
    "validate_bar",
    "validate_ordering",
    "validate_quote",
    "validate_snapshot",
    "validate_tick",
    "weighted_mid",
]
