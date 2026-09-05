"""Guards the v2.3 market-data convergence against silent re-duplication.

Before v2.3 five market-data surfaces coexisted:

* ``alphalab.market``            -- Decimal / ``asset_id``, on the execution path
* ``alphalab.data.feed``         -- float / ``symbol`` wire records
* ``alphalab.marketdata.feed``   -- a second, field-for-field identical copy
* ``alphalab.live.message``      -- provider-tagged wire messages
* ``alphalab.feed.normalization``-- raw dicts lifted into ``alphalab.market``

v2.3 keeps the ones that answer different questions and collapses the ones that
did not. These tests assert *identity*, not similarity: two dataclasses can have
identical fields and still be different types, and that is exactly the failure
mode being guarded against.
"""

from decimal import Decimal

from alphalab.data import feed as wire
from alphalab.live import message as live_message
from alphalab.market import bar as market_bar
from alphalab.market import quote as market_quote
from alphalab.marketdata import feed as marketdata_feed


def test_marketdata_wire_records_are_the_data_wire_records() -> None:
    """One definition per wire concept, not two identical ones."""
    assert marketdata_feed.Quote is wire.Quote
    assert marketdata_feed.Trade is wire.Trade
    assert marketdata_feed.Bar is wire.Bar
    assert marketdata_feed.OrderBook is wire.OrderBook
    assert marketdata_feed.OrderBookLevel is wire.OrderBookLevel


def test_live_book_level_is_the_shared_wire_level() -> None:
    """The one live message shape that carried no provider tag was a duplicate."""
    assert live_message.OrderBookLevel is wire.OrderBookLevel


def test_the_public_marketdata_api_still_exposes_the_same_names() -> None:
    """Convergence must not have removed anything importable."""
    import alphalab.marketdata as md

    assert (md.Quote, md.Trade, md.Bar, md.OrderBook, md.OrderBookLevel) == (
        wire.Quote,
        wire.Trade,
        wire.Bar,
        wire.OrderBook,
        wire.OrderBookLevel,
    )


def test_a_wire_bar_built_either_way_is_one_interchangeable_value() -> None:
    """Constructed through either import path, the values are equal and same-typed."""
    from alphalab.data.feed import Bar as DataBar
    from alphalab.marketdata.feed import Bar as ProviderBar

    built_by_data = DataBar("AAPL", 1000.0, 1.0, 2.0, 0.5, 1.5, 10.0)
    built_by_provider = ProviderBar("AAPL", 1000.0, 1.0, 2.0, 0.5, 1.5, 10.0)

    assert built_by_data == built_by_provider
    assert type(built_by_data) is type(built_by_provider)


def test_wire_records_share_one_identity_base() -> None:
    """Every wire record answers "which instrument, and when" the same way."""
    assert issubclass(wire.Quote, wire.CanonicalRecord)
    assert issubclass(marketdata_feed.Bar, wire.CanonicalRecord)


def test_the_domain_bar_and_the_wire_bar_remain_deliberately_distinct() -> None:
    """The one duplicate that is not a duplicate: different layers, different types.

    ``market.Bar`` is the canonical execution-path bar -- Decimal, ``asset_id``,
    with a timeframe, a vwap and a trade count. ``data.Bar`` is the transport
    shape a provider can fill in without knowing any of that. Collapsing them
    would force one of the two to lie.
    """
    # Distinct definitions in distinct layers. Compared through `type[object]`
    # because mypy already knows these are different types -- which is itself
    # part of the guarantee -- and would reject a direct identity check.
    domain: type[object] = market_bar.Bar
    transport: type[object] = wire.Bar
    assert domain is not transport
    assert (domain.__module__, transport.__module__) == (
        "alphalab.market.bar",
        "alphalab.data.feed",
    )

    domain_fields = set(market_bar.Bar.__dataclass_fields__)
    wire_fields = set(wire.Bar.__dataclass_fields__)

    assert "asset_id" in domain_fields and "symbol" in wire_fields
    assert {"timeframe", "vwap", "trade_count"} <= domain_fields
    assert not {"timeframe", "vwap", "trade_count"} & wire_fields


def test_the_domain_quote_carries_venue_and_currency_the_wire_quote_cannot() -> None:
    domain: type[object] = market_quote.Quote
    transport: type[object] = wire.Quote
    assert domain is not transport

    domain_fields = set(market_quote.Quote.__dataclass_fields__)
    assert {"venue", "currency", "asset_id"} <= domain_fields
    assert not {"venue", "currency"} & set(wire.Quote.__dataclass_fields__)


def test_the_domain_book_level_carries_an_order_count_the_wire_level_does_not() -> None:
    from alphalab.market.level import OrderBookLevel as DomainLevel

    domain: type[object] = DomainLevel
    transport: type[object] = wire.OrderBookLevel
    assert domain is not transport

    assert "orders" in DomainLevel.__dataclass_fields__
    assert "orders" not in wire.OrderBookLevel.__dataclass_fields__


def test_domain_records_use_decimal_and_wire_records_use_float() -> None:
    """The layer split is exactly the money-type split."""
    domain = market_quote.Quote(
        "AAPL", 1.0, Decimal("1"), Decimal("2"), Decimal("1"), Decimal("1"), "SIM", "USD"
    )
    transport = wire.Quote("AAPL", 1.0, 1.0, 2.0, 1.0, 1.0)

    assert isinstance(domain.bid, Decimal)
    assert isinstance(transport.bid, float)


def test_the_canonical_record_is_defined_in_the_market_package() -> None:
    """A live adapter must be able to produce records without importing backtesting."""
    from alphalab.backtesting.dataset import MarketRecord as BacktestRecord
    from alphalab.market.record import MarketRecord as CanonicalRecord

    assert BacktestRecord is CanonicalRecord
    assert CanonicalRecord.__module__ == "alphalab.market.record"


def test_the_dict_normalizers_still_produce_canonical_domain_records() -> None:
    """`alphalab.feed` predates v2.3 and still lifts raw dicts into the same types."""
    from alphalab.feed.normalization import RawPayload, normalize_quote

    quote = normalize_quote(
        RawPayload(
            "QUOTE", {"symbol": "AAPL", "ts": 1.0, "bid": 1, "ask": 2, "bid_size": 1, "ask_size": 1}
        ),
        "XNAS",
    )
    assert type(quote) is market_quote.Quote
