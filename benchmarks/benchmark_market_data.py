"""Market-data ingestion and normalization throughput (v2.3).

Measures the two costs the v2.3 market-data layer adds or removes:

1. **Normalization** -- lifting a provider wire record into the canonical
   domain record. This is per-record work that did not exist before v2.3, so it
   has to be cheap enough to sit in front of a feed.
2. **Ingestion** -- publishing canonical records into the market engine, across
   universe sizes. Before v2.3 this rebuilt the whole ``latest_*`` index on
   every publish, so throughput fell in proportion to the universe. The
   persistent indexes make it flat, and the sweep below is what shows that.
"""

import time
from decimal import Decimal

from alphalab.data.feed import Bar as WireBar
from alphalab.data.feed import OrderBook as WireOrderBook
from alphalab.data.feed import OrderBookLevel as WireLevel
from alphalab.data.feed import Quote as WireQuote
from alphalab.data.feed import Trade as WireTrade
from alphalab.market.bar import TimeFrame
from alphalab.market.engine import MarketEngine
from alphalab.market.normalization import (
    NormalizationPolicy,
    normalize_wire_bar,
    normalize_wire_book,
    normalize_wire_quote,
    normalize_wire_trade,
)
from alphalab.market.state import MarketState

POLICY = NormalizationPolicy(venue="BENCH", currency="USD", timeframe=TimeFrame.M1)
N = 100_000
BOOK_N = 20_000
BOOK_DEPTH = 10


def _report(label: str, count: int, seconds: float) -> None:
    print(f"{label:<34} {seconds:8.4f}s  {count / seconds:>12,.0f} rec/sec")


def benchmark_normalization() -> None:
    """Per-record cost of the wire -> canonical boundary."""

    print(f"\nNormalization ({N:,} records each)")
    print("-" * 62)

    quotes = tuple(WireQuote(f"S{i % 500}", float(i + 1), 10.0, 10.1, 5.0, 7.0) for i in range(N))
    start = time.perf_counter()
    for quote in quotes:
        normalize_wire_quote(quote, POLICY)
    _report("quote", N, time.perf_counter() - start)

    trades = tuple(WireTrade(f"S{i % 500}", float(i + 1), 10.05, 3.0) for i in range(N))
    start = time.perf_counter()
    for trade in trades:
        normalize_wire_trade(trade, POLICY)
    _report("trade", N, time.perf_counter() - start)

    bars = tuple(
        WireBar(f"S{i % 500}", float(i + 1), 10.0, 10.5, 9.5, 10.2, 1000.0) for i in range(N)
    )
    start = time.perf_counter()
    for bar in bars:
        normalize_wire_bar(bar, POLICY)
    _report("bar", N, time.perf_counter() - start)

    levels = tuple(WireLevel(float(10 - i), float(100 + i)) for i in range(BOOK_DEPTH))
    asks = tuple(WireLevel(float(11 + i), float(100 + i)) for i in range(BOOK_DEPTH))
    books = tuple(WireOrderBook(f"S{i % 500}", float(i + 1), levels, asks) for i in range(BOOK_N))
    start = time.perf_counter()
    for index, book in enumerate(books):
        normalize_wire_book(book, POLICY, index + 1)
    _report(f"order book ({BOOK_DEPTH} levels/side)", BOOK_N, time.perf_counter() - start)


def benchmark_ingestion() -> None:
    """Publishing canonical records, swept across universe size.

    A flat sweep is the result being checked: publishing must not get slower
    because more instruments exist.
    """

    print(f"\nIngestion into the market engine ({N:,} quotes, by universe size)")
    print("-" * 62)

    for universe in (1, 100, 1_000, 10_000):
        quotes = tuple(
            normalize_wire_quote(
                WireQuote(f"S{i % universe}", float(i + 1), 10.0, 10.1, 5.0, 7.0), POLICY
            )
            for i in range(N)
        )
        state: MarketState = MarketEngine.reset()
        start = time.perf_counter()
        for quote in quotes:
            state = MarketEngine.publish_quote(state, quote)
        _report(f"quotes, universe {universe:,}", N, time.perf_counter() - start)
        assert len(state.latest_quotes) == universe


def benchmark_mixed_ingestion() -> None:
    """Trades and bars, which take the same path as quotes."""

    print(f"\nIngestion by record type ({N:,} records, universe 1,000)")
    print("-" * 62)

    ticks = tuple(
        normalize_wire_trade(WireTrade(f"S{i % 1000}", float(i + 1), 10.05, 3.0), POLICY)
        for i in range(N)
    )
    state: MarketState = MarketEngine.reset()
    start = time.perf_counter()
    for tick in ticks:
        state = MarketEngine.publish_tick(state, tick)
    _report("trades", N, time.perf_counter() - start)

    bars = tuple(
        normalize_wire_bar(
            WireBar(f"S{i % 1000}", float(i + 1), 10.0, 10.5, 9.5, 10.2, 1000.0), POLICY
        )
        for i in range(N)
    )
    state = MarketEngine.reset()
    start = time.perf_counter()
    for bar in bars:
        state = MarketEngine.publish_bar(state, bar)
    _report("bars", N, time.perf_counter() - start)


def benchmark_end_to_end() -> None:
    """Wire record in, canonical record published -- the whole feed path."""

    print(f"\nEnd to end: wire quote -> normalize -> publish ({N:,} records)")
    print("-" * 62)

    wire = tuple(WireQuote(f"S{i % 1000}", float(i + 1), 10.0, 10.1, 5.0, 7.0) for i in range(N))
    state: MarketState = MarketEngine.reset()
    start = time.perf_counter()
    for quote in wire:
        state = MarketEngine.publish_quote(state, normalize_wire_quote(quote, POLICY))
    _report("normalize + publish", N, time.perf_counter() - start)

    assert state.latest_quotes["S0"].bid == Decimal("10.0")


def run_benchmark() -> None:
    print("=" * 62)
    print("AlphaLab v2.3 Market Data Benchmark")
    print("=" * 62)
    benchmark_normalization()
    benchmark_ingestion()
    benchmark_mixed_ingestion()
    benchmark_end_to_end()


if __name__ == "__main__":
    run_benchmark()
