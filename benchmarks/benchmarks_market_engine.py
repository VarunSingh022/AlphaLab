"""High-performance benchmarking suite for the functional immutable Market Data Engine."""

import time
from decimal import Decimal

from alphalab.market import (
    MarketEngine,
    OrderBookLevel,
    OrderBookSnapshot,
    Quote,
    latest_book,
    latest_quote,
)


def run_benchmark() -> None:
    state = MarketEngine.reset()
    N = 100_000

    bids = (OrderBookLevel(Decimal("100"), Decimal("10"), 1),)
    asks = (OrderBookLevel(Decimal("101"), Decimal("10"), 1),)

    print(f"Starting Market Data Benchmark: {N} operations...")

    # 1. Quote Publishing
    start = time.perf_counter()
    for i in range(N):
        q = Quote(
            "AAPL",
            float(i),
            Decimal("100"),
            Decimal("101"),
            Decimal("1"),
            Decimal("1"),
            "SIM",
            "USD",
        )
        state = MarketEngine.publish_quote(state, q)
    quote_time = time.perf_counter() - start
    print(f"Quote Publishing: {N / quote_time:.2f} ops/sec")

    # 2. Book Publishing
    start = time.perf_counter()
    for i in range(N):
        book = OrderBookSnapshot("AAPL", float(i), bids, asks, i + 1)
        state = MarketEngine.publish_book(state, book)
    book_time = time.perf_counter() - start
    print(f"Book Publishing: {N / book_time:.2f} ops/sec")

    # 3. View Lookups
    start = time.perf_counter()
    for _ in range(N):
        _ = latest_quote(state, "AAPL")
        _ = latest_book(state, "AAPL")
    lookup_time = time.perf_counter() - start
    print(f"State Lookups (Quote+Book pairs): {N / lookup_time:.2f} ops/sec")

    # 4. Total Memory/Events generated check
    print(f"Total events emitted structurally: {len(state.events)}")


if __name__ == "__main__":
    run_benchmark()
