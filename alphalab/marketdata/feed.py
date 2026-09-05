"""Wire records this package's providers produce.

Before v2.3 this module defined ``Quote``, ``Trade``, ``Bar``, ``OrderBookLevel``
and ``OrderBook`` as its own dataclasses, field-for-field identical to the ones
in :mod:`alphalab.data.feed`. Two identical definitions of one concept is not a
distinction, it is drift waiting to happen: a provider adapter and the data
engine could not exchange a bar without a copy, and a fix applied to one copy
would silently miss the other.

There is now one definition, in :mod:`alphalab.data.feed`, re-exported here. The
names, fields and semantics are unchanged, so ``from alphalab.marketdata.feed
import Bar`` keeps working -- and now returns the same class the data engine
uses.

These are *wire* records: ``float`` prices keyed by provider ``symbol``. See
:mod:`alphalab.market.normalization` for the boundary that lifts them into the
canonical ``Decimal`` / ``asset_id`` domain records the execution path consumes.
"""

from alphalab.data.feed import (
    Bar,
    CanonicalRecord,
    OrderBook,
    OrderBookLevel,
    Quote,
    Trade,
)

__all__ = [
    "Bar",
    "CanonicalRecord",
    "OrderBook",
    "OrderBookLevel",
    "Quote",
    "Trade",
]
