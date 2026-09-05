"""The wire -> canonical boundary, and the rules it applies.

Everything that reaches the execution path crosses this module. A provider hands
AlphaLab a :mod:`alphalab.data.feed` wire record -- ``float`` prices keyed by a
provider ``symbol`` -- and this boundary lifts it into the canonical domain
record the execution path consumes: ``Decimal`` prices keyed by ``asset_id``,
carrying the venue and currency the wire shape has no room for.

Why the conversion is not incidental
------------------------------------

``float`` is the wrong type for money and this is the only place that stops
being true. ``Decimal(str(value))`` is used deliberately: ``Decimal(0.1)`` is
``0.1000000000000000055511151231257827021181583404541015625``, while
``Decimal(str(0.1))`` is ``Decimal("0.1")``. Going through ``str`` reproduces the
number the provider meant, not the binary approximation that reached memory.
Every conversion below does this, which is what makes normalization
deterministic: the same wire record always produces the same canonical record,
byte for byte.

The rules
---------

============== ==========================================================
Timestamps     Unix seconds as ``float``, passed through unchanged. Must be
               strictly positive (:func:`alphalab.market.timestamp.is_valid_timestamp`).
Prices/sizes   ``Decimal`` via ``str``. Never quantized here -- the venue's
               own precision is preserved and rounding stays a downstream
               decision.
Identity       The provider ``symbol`` becomes ``asset_id`` verbatim by
               default. A :class:`SymbolMap` can rewrite it when a venue's
               symbol is not AlphaLab's asset id.
Venue/currency Not present on the wire; supplied by the
               :class:`NormalizationPolicy` doing the lifting.
Bar timeframe  Not present on the wire; supplied by the policy. ``vwap`` and
               ``trade_count`` default to ``0`` / ``0`` because a wire bar
               carries neither -- absent, not zero-valued, and readers should
               treat them as unknown.
Trade side     Not represented. Wire trades carry no aggressor flag, so the
               canonical :class:`~alphalab.market.tick.Tick` records the print
               without inferring a direction.
Book levels    ``orders`` defaults to ``0``: the wire level has no order count.
               Levels are passed through in the order the provider sent them.
Validation     Every canonical record is validated on the way out
               (:mod:`alphalab.market.validation`), so an invalid wire record
               fails here rather than deeper in the execution path.
============== ==========================================================

Missing, stale and invalid data
-------------------------------

Three different failures, three different answers:

* **Invalid** -- a crossed quote, a negative size, a non-positive timestamp.
  Raises :class:`~alphalab.market.exceptions.MarketValidationError`. The record
  is not representable and no downstream default would be honest.
* **Missing** -- a field the wire shape has no room for (venue, currency,
  timeframe, vwap, order counts). Supplied by the policy or defaulted, and
  documented above as unknown rather than measured.
* **Stale** -- a well-formed record that is simply too old to act on. Not an
  error: :func:`is_stale` and :func:`reject_stale` let a caller decide, because
  what counts as stale is a property of the strategy, not of the data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.data.feed import Bar as WireBar
from alphalab.data.feed import OrderBook as WireOrderBook
from alphalab.data.feed import OrderBookLevel as WireOrderBookLevel
from alphalab.data.feed import Quote as WireQuote
from alphalab.data.feed import Trade as WireTrade
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.level import OrderBookLevel
from alphalab.market.quote import Quote
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.tick import Tick
from alphalab.market.validation import (
    validate_bar,
    validate_quote,
    validate_snapshot,
    validate_tick,
)

__all__ = [
    "DEFAULT_POLICY",
    "NormalizationPolicy",
    "SymbolMap",
    "is_stale",
    "normalize_wire_bar",
    "normalize_wire_book",
    "normalize_wire_quote",
    "normalize_wire_trade",
    "reject_stale",
    "to_decimal",
]


def to_decimal(value: float | str | Decimal) -> Decimal:
    """Convert a wire number to ``Decimal`` without inheriting binary error.

    Routing through ``str`` is what makes this exact: ``Decimal(0.1)`` keeps the
    float's binary expansion, ``Decimal(str(0.1))`` keeps the number the provider
    wrote.
    """

    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class SymbolMap:
    """Provider symbol -> AlphaLab ``asset_id``.

    Unmapped symbols pass through unchanged, which is the common case: most
    venues and AlphaLab already agree on the ticker.
    """

    mapping: Mapping[str, str] = field(default_factory=dict)

    def asset_id(self, symbol: str) -> str:
        """The asset id ``symbol`` denotes."""

        return self.mapping.get(symbol, symbol)


@dataclass(frozen=True, slots=True)
class NormalizationPolicy:
    """What the wire shape cannot say, and this venue's answer for it.

    Attributes:
        venue: Venue recorded on canonical quotes and ticks.
        currency: Currency recorded on canonical quotes and ticks.
        timeframe: Timeframe recorded on canonical bars.
        symbols: Provider-symbol to ``asset_id`` mapping.
    """

    venue: str = "UNKNOWN"
    currency: str = "USD"
    timeframe: TimeFrame = TimeFrame.M1
    symbols: SymbolMap = field(default_factory=SymbolMap)

    def asset_id(self, symbol: str) -> str:
        """The asset id this policy assigns to a provider ``symbol``."""

        return self.symbols.asset_id(symbol)


#: Policy used when a caller supplies none. Names the venue ``"UNKNOWN"`` rather
#: than guessing one, so an unattributed record stays visibly unattributed.
DEFAULT_POLICY = NormalizationPolicy()


def normalize_wire_quote(quote: WireQuote, policy: NormalizationPolicy = DEFAULT_POLICY) -> Quote:
    """Lift a wire quote into the canonical top-of-book quote."""

    canonical = Quote(
        asset_id=policy.asset_id(quote.symbol),
        timestamp=quote.timestamp,
        bid=to_decimal(quote.bid),
        ask=to_decimal(quote.ask),
        bid_size=to_decimal(quote.bid_size),
        ask_size=to_decimal(quote.ask_size),
        venue=policy.venue,
        currency=policy.currency,
    )
    validate_quote(canonical)
    return canonical


def normalize_wire_trade(
    trade: WireTrade,
    policy: NormalizationPolicy = DEFAULT_POLICY,
    trade_id: str = "",
) -> Tick:
    """Lift a wire trade print into the canonical tick.

    A wire trade carries no identifier and no aggressor side. ``trade_id``
    defaults to empty rather than being invented, and no direction is inferred.
    """

    canonical = Tick(
        asset_id=policy.asset_id(trade.symbol),
        timestamp=trade.timestamp,
        price=to_decimal(trade.price),
        quantity=to_decimal(trade.size),
        trade_id=trade_id,
        venue=policy.venue,
        currency=policy.currency,
    )
    validate_tick(canonical)
    return canonical


def normalize_wire_bar(bar: WireBar, policy: NormalizationPolicy = DEFAULT_POLICY) -> Bar:
    """Lift a wire OHLCV bar into the canonical bar.

    ``vwap`` and ``trade_count`` are set to zero because the wire bar carries
    neither. They mean "not reported", not "zero".
    """

    canonical = Bar(
        asset_id=policy.asset_id(bar.symbol),
        timestamp=bar.timestamp,
        open=to_decimal(bar.open),
        high=to_decimal(bar.high),
        low=to_decimal(bar.low),
        close=to_decimal(bar.close),
        volume=to_decimal(bar.volume),
        vwap=Decimal("0"),
        trade_count=0,
        timeframe=policy.timeframe,
    )
    validate_bar(canonical)
    return canonical


def normalize_wire_book(
    book: WireOrderBook,
    policy: NormalizationPolicy = DEFAULT_POLICY,
    sequence: int = 1,
) -> OrderBookSnapshot:
    """Lift a wire depth book into the canonical snapshot.

    Wire books carry no sequence number, so the caller supplies one. Sequence
    matters downstream: :meth:`~alphalab.market.engine.MarketEngine.publish_book`
    refuses a book whose sequence does not advance, which is how duplicate and
    out-of-order depth updates are rejected.
    """

    canonical = OrderBookSnapshot(
        asset_id=policy.asset_id(book.symbol),
        timestamp=book.timestamp,
        bids=_levels(book.bids),
        asks=_levels(book.asks),
        sequence=sequence,
    )
    validate_snapshot(canonical)
    return canonical


def _levels(levels: Sequence[WireOrderBookLevel]) -> tuple[OrderBookLevel, ...]:
    """Convert wire levels, preserving provider order. ``orders`` is unreported."""

    return tuple(
        OrderBookLevel(price=to_decimal(level.price), size=to_decimal(level.size), orders=0)
        for level in levels
    )


def is_stale(timestamp: float, now: float, max_age_seconds: float) -> bool:
    """Whether a record timestamped ``timestamp`` is too old at ``now``.

    A record from the future is never stale. Staleness is age, not disagreement
    about the clock.
    """

    if max_age_seconds < 0.0:
        raise ValueError("max_age_seconds must be non-negative")
    return (now - timestamp) > max_age_seconds


def reject_stale(timestamp: float, now: float, max_age_seconds: float, what: str) -> None:
    """Raise if a record is too old to act on.

    Separate from validation on purpose: a stale record is well-formed, and only
    the caller knows how old is too old.
    """

    if is_stale(timestamp, now, max_age_seconds):
        raise MarketValidationError(
            f"Stale {what}: timestamped {timestamp}, now {now}, "
            f"which exceeds the {max_age_seconds}s limit."
        )
