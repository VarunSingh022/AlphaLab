"""Comprehensive test suite for the Market Data Engine."""

from decimal import Decimal

import pytest

from alphalab.market import (
    Bar,
    MarketEngine,
    MarketValidationError,
    OrderBookLevel,
    OrderBookSnapshot,
    Quote,
    Tick,
    TimeFrame,
    best_ask,
    best_bid,
    latest_bar,
    latest_book,
    latest_quote,
    latest_tick,
    mid_price,
    spread,
    weighted_mid,
)


def test_quote_publishing_and_views() -> None:
    state = MarketEngine.reset()
    q = Quote(
        "AAPL",
        1600000000.0,
        Decimal("150.0"),
        Decimal("150.5"),
        Decimal("10"),
        Decimal("20"),
        "SIM",
        "USD",
    )

    state = MarketEngine.publish_quote(state, q)
    assert latest_quote(state, "AAPL") == q
    assert latest_quote(state, "MSFT") is None


def test_tick_publishing() -> None:
    state = MarketEngine.reset()
    t = Tick("AAPL", 1600000000.0, Decimal("150.25"), Decimal("100"), "TRD1", "SIM", "USD")

    state = MarketEngine.publish_tick(state, t)
    assert latest_tick(state, "AAPL") == t


def test_bar_publishing() -> None:
    state = MarketEngine.reset()
    b = Bar(
        "AAPL",
        1600000000.0,
        Decimal("150"),
        Decimal("155"),
        Decimal("149"),
        Decimal("154"),
        Decimal("1000"),
        Decimal("152"),
        100,
        TimeFrame.M1,
    )

    state = MarketEngine.publish_bar(state, b)
    assert latest_bar(state, "AAPL", "1m") == b


def test_book_publishing_and_sequence() -> None:
    state = MarketEngine.reset()
    bids = (OrderBookLevel(Decimal("100"), Decimal("10"), 1),)
    asks = (OrderBookLevel(Decimal("101"), Decimal("20"), 1),)
    book1 = OrderBookSnapshot("AAPL", 1600000000.0, bids, asks, 1)
    book2 = OrderBookSnapshot("AAPL", 1600000001.0, bids, asks, 2)

    state = MarketEngine.publish_snapshot(state, book1)
    assert latest_book(state, "AAPL") == book1

    state = MarketEngine.publish_book(state, book2)
    assert latest_book(state, "AAPL") == book2

    # Reject duplicate/backwards sequence
    book3 = OrderBookSnapshot("AAPL", 1600000002.0, bids, asks, 1)
    with pytest.raises(MarketValidationError, match="Duplicate or backward sequence"):
        MarketEngine.publish_book(state, book3)


def test_book_calculations() -> None:
    bids = (
        OrderBookLevel(Decimal("100.00"), Decimal("10.0"), 1),
        OrderBookLevel(Decimal("99.00"), Decimal("50.0"), 1),
    )
    asks = (
        OrderBookLevel(Decimal("101.00"), Decimal("20.0"), 1),
        OrderBookLevel(Decimal("102.00"), Decimal("30.0"), 1),
    )
    snapshot = OrderBookSnapshot("AAPL", 1000.0, bids, asks, 1)

    assert best_bid(snapshot) == Decimal("100.00")
    assert best_ask(snapshot) == Decimal("101.00")
    assert spread(snapshot) == Decimal("1.00")
    assert mid_price(snapshot) == Decimal("100.50")

    # Weighted mid: (100*20 + 101*10) / 30 = (2000 + 1010) / 30 = 3010 / 30 = 100.333333
    assert weighted_mid(snapshot) == Decimal("100.333333")


def test_validation_failures() -> None:
    state = MarketEngine.reset()

    # Negative spread quote
    bad_quote = Quote(
        "AAPL",
        1000.0,
        Decimal("150.0"),
        Decimal("149.0"),
        Decimal("10"),
        Decimal("10"),
        "SIM",
        "USD",
    )
    with pytest.raises(MarketValidationError, match="Negative spread"):
        MarketEngine.publish_quote(state, bad_quote)

    # Negative price tick
    bad_tick = Tick("AAPL", 1000.0, Decimal("-10"), Decimal("100"), "T1", "SIM", "USD")
    with pytest.raises(MarketValidationError, match="Tick price cannot be negative"):
        MarketEngine.publish_tick(state, bad_tick)

    # Crossed Book
    bids = (OrderBookLevel(Decimal("100"), Decimal("10"), 1),)
    asks = (OrderBookLevel(Decimal("99"), Decimal("10"), 1),)
    bad_book = OrderBookSnapshot("AAPL", 1000.0, bids, asks, 1)
    with pytest.raises(MarketValidationError, match="Crossed book in snapshot"):
        MarketEngine.publish_book(state, bad_book)


def test_immutability() -> None:
    state1 = MarketEngine.reset()
    q = Quote(
        "AAPL",
        1000.0,
        Decimal("150"),
        Decimal("151"),
        Decimal("1"),
        Decimal("1"),
        "SIM",
        "USD",
    )

    state2 = MarketEngine.publish_quote(state1, q)
    assert state1 is not state2
    assert latest_quote(state1, "AAPL") is None
    assert latest_quote(state2, "AAPL") is not None
