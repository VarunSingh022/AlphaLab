"""The wire -> canonical boundary: identity, precision, and what it refuses."""

from decimal import Decimal

import pytest

from alphalab.data.feed import Bar as WireBar
from alphalab.data.feed import OrderBook as WireOrderBook
from alphalab.data.feed import OrderBookLevel as WireLevel
from alphalab.data.feed import Quote as WireQuote
from alphalab.data.feed import Trade as WireTrade
from alphalab.market.bar import TimeFrame
from alphalab.market.exceptions import MarketValidationError
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

_POLICY = NormalizationPolicy(venue="XNAS", currency="USD", timeframe=TimeFrame.M5)


def test_to_decimal_routes_through_str_so_binary_error_is_not_inherited() -> None:
    """The whole reason normalization is not a cast: 0.1 must stay 0.1."""
    # Constructing a Decimal straight from the float is the mistake this
    # boundary exists to avoid, so it is written out here deliberately.
    binary_expansion = Decimal(0.1)  # noqa: RUF032

    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(0.1) != binary_expansion
    assert to_decimal(0.3) - to_decimal(0.1) == Decimal("0.2")


def test_to_decimal_passes_decimals_through_unchanged() -> None:
    value = Decimal("1.2345678901234567890")
    assert to_decimal(value) is value


def test_normalize_quote_lifts_every_field_and_supplies_what_the_wire_lacks() -> None:
    quote = normalize_wire_quote(WireQuote("AAPL", 1000.0, 10.25, 10.75, 300.0, 400.0), _POLICY)

    assert quote.asset_id == "AAPL"
    assert quote.timestamp == 1000.0
    assert (quote.bid, quote.ask) == (Decimal("10.25"), Decimal("10.75"))
    assert (quote.bid_size, quote.ask_size) == (Decimal("300.0"), Decimal("400.0"))
    # Venue and currency have no wire representation; the policy supplies them.
    assert quote.venue == "XNAS"
    assert quote.currency == "USD"


def test_normalize_trade_invents_neither_id_nor_direction() -> None:
    """A wire print carries no trade id and no aggressor flag."""
    tick = normalize_wire_trade(WireTrade("AAPL", 1000.0, 10.5, 25.0), _POLICY)

    assert tick.price == Decimal("10.5")
    assert tick.quantity == Decimal("25.0")
    assert tick.trade_id == ""
    assert not hasattr(tick, "side")


def test_normalize_trade_accepts_a_supplied_trade_id() -> None:
    tick = normalize_wire_trade(WireTrade("AAPL", 1000.0, 10.5, 25.0), _POLICY, "T-1")
    assert tick.trade_id == "T-1"


def test_normalize_bar_marks_unreported_fields_as_unreported() -> None:
    bar = normalize_wire_bar(WireBar("AAPL", 1000.0, 10.0, 12.0, 9.0, 11.0, 5000.0), _POLICY)

    assert (bar.open, bar.high, bar.low, bar.close) == (
        Decimal("10.0"),
        Decimal("12.0"),
        Decimal("9.0"),
        Decimal("11.0"),
    )
    assert bar.volume == Decimal("5000.0")
    # Neither is on the wire: zero here means "not reported".
    assert bar.vwap == Decimal("0")
    assert bar.trade_count == 0
    # The timeframe is the policy's, not a guess from the data.
    assert bar.timeframe is TimeFrame.M5


def test_normalize_book_preserves_level_order_and_reports_no_order_count() -> None:
    wire = WireOrderBook(
        "AAPL",
        1000.0,
        bids=(WireLevel(10.0, 5.0), WireLevel(9.5, 7.0)),
        asks=(WireLevel(10.5, 3.0), WireLevel(11.0, 2.0)),
    )
    book = normalize_wire_book(wire, _POLICY, sequence=42)

    assert [level.price for level in book.bids] == [Decimal("10.0"), Decimal("9.5")]
    assert [level.price for level in book.asks] == [Decimal("10.5"), Decimal("11.0")]
    assert all(level.orders == 0 for level in (*book.bids, *book.asks))
    assert book.sequence == 42


def test_symbol_map_rewrites_only_what_it_maps() -> None:
    policy = NormalizationPolicy(symbols=SymbolMap({"AAPL.US": "AAPL"}))

    mapped = normalize_wire_quote(WireQuote("AAPL.US", 1.0, 1.0, 2.0, 1.0, 1.0), policy)
    passthrough = normalize_wire_quote(WireQuote("MSFT", 1.0, 1.0, 2.0, 1.0, 1.0), policy)

    assert mapped.asset_id == "AAPL"
    assert passthrough.asset_id == "MSFT"


def test_default_policy_does_not_invent_a_venue() -> None:
    quote = normalize_wire_quote(WireQuote("AAPL", 1.0, 1.0, 2.0, 1.0, 1.0), DEFAULT_POLICY)
    assert quote.venue == "UNKNOWN"


@pytest.mark.parametrize(
    ("wire", "reason"),
    [
        (WireQuote("AAPL", 1000.0, 11.0, 10.0, 1.0, 1.0), "crossed"),
        (WireQuote("AAPL", 1000.0, -1.0, 10.0, 1.0, 1.0), "negative price"),
        (WireQuote("AAPL", 1000.0, 1.0, 10.0, -1.0, 1.0), "negative size"),
        (WireQuote("AAPL", 0.0, 1.0, 10.0, 1.0, 1.0), "non-positive timestamp"),
    ],
)
def test_invalid_wire_quotes_are_refused_at_the_boundary(wire: WireQuote, reason: str) -> None:
    """Invalid data fails here, not deeper in the execution path."""
    with pytest.raises(MarketValidationError):
        normalize_wire_quote(wire, _POLICY)


def test_invalid_wire_bar_is_refused() -> None:
    with pytest.raises(MarketValidationError):
        normalize_wire_bar(WireBar("AAPL", 1000.0, 10.0, 9.0, 12.0, 11.0, 1.0), _POLICY)


def test_crossed_wire_book_is_refused() -> None:
    wire = WireOrderBook("AAPL", 1000.0, bids=(WireLevel(11.0, 1.0),), asks=(WireLevel(10.0, 1.0),))
    with pytest.raises(MarketValidationError):
        normalize_wire_book(wire, _POLICY)


def test_empty_book_sides_are_allowed_not_invented() -> None:
    """A one-sided book is legitimate; normalization must not fabricate a side."""
    book = normalize_wire_book(WireOrderBook("AAPL", 1000.0, bids=(WireLevel(10.0, 1.0),)), _POLICY)
    assert book.bids and not book.asks


def test_staleness_is_age_not_a_clock_disagreement() -> None:
    assert is_stale(timestamp=100.0, now=106.0, max_age_seconds=5.0)
    assert not is_stale(timestamp=100.0, now=104.0, max_age_seconds=5.0)
    # Exactly at the limit is not yet stale.
    assert not is_stale(timestamp=100.0, now=105.0, max_age_seconds=5.0)
    # A record from the future is never stale.
    assert not is_stale(timestamp=200.0, now=100.0, max_age_seconds=5.0)


def test_negative_staleness_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        is_stale(1.0, 2.0, -1.0)


def test_reject_stale_raises_only_when_stale() -> None:
    reject_stale(100.0, 104.0, 5.0, "quote")
    with pytest.raises(MarketValidationError, match="Stale quote"):
        reject_stale(100.0, 106.0, 5.0, "quote")


def test_normalization_is_deterministic() -> None:
    """The same wire record always produces an equal canonical record."""
    wire = WireQuote("AAPL", 1000.0, 10.25, 10.75, 300.0, 400.0)
    assert normalize_wire_quote(wire, _POLICY) == normalize_wire_quote(wire, _POLICY)
