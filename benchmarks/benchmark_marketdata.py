"""High-performance benchmarking suite for the functional Market Data Engine.

This benchmark could not run before v2.3, for two separate reasons, both fixed:

1. It connected through ``YahooAdapter``, whose client raises
   ``NotImplementedError`` -- the honest state of every vendor client in this
   repository. It now uses an explicit in-benchmark double, which is what a
   benchmark of *the engine* should have depended on all along. No vendor
   connectivity is faked.
2. ``MarketDataState`` grew its event history with ``(*state.events, evt)``,
   so 100k ticks copied O(N^2) events. Persistent containers make each message
   O(1) amortized.
"""

import time
from typing import Any

from alphalab.marketdata import MarketDataEngine, ProviderConfig, Trade
from alphalab.marketdata.feed import Bar, OrderBook, Quote
from alphalab.marketdata.timeframe import Timeframe


class BenchmarkProvider:
    """A deterministic stand-in satisfying ``MarketDataProtocol``.

    Explicitly a test double, not a vendor: it connects, subscribes and reports
    healthy so the engine's own lifecycle can be exercised, and returns nothing
    for data requests rather than fabricating any.
    """

    def connect(self) -> bool:
        return True

    def disconnect(self) -> bool:
        return True

    def subscribe(self, symbol: str, timeframe: Timeframe) -> bool:
        return True

    def unsubscribe(self, symbol: str, timeframe: Timeframe) -> bool:
        return True

    def request_history(
        self, symbol: str, timeframe: Timeframe, start: float, end: float
    ) -> tuple[Bar, ...]:
        return ()

    def latest_quote(self, symbol: str) -> Quote | None:
        return None

    def latest_trade(self, symbol: str) -> Trade | None:
        return None

    def latest_bar(self, symbol: str) -> Bar | None:
        return None

    def order_book(self, symbol: str) -> OrderBook | None:
        return None

    def health(self) -> dict[str, Any]:
        return {"status": "healthy", "latency_ms": 0.0}


def run_benchmark() -> None:
    N = 100_000
    print(f"Starting Market Data Infrastructure Benchmark: Processing {N} Trades...")

    state = MarketDataEngine.initialize("MD-BENCH")
    cfg = ProviderConfig("BENCH", "Benchmark Double", "bench")
    provider = BenchmarkProvider()

    state = MarketDataEngine.register(state, cfg, 1000.0)
    state = MarketDataEngine.connect(state, "BENCH", provider, 1001.0)

    trades = tuple(Trade(f"S{i % 500}", float(1002 + i), 150.0 + (i % 10), 100.0) for i in range(N))

    start = time.perf_counter()

    for i in range(N):
        state = MarketDataEngine.process_trade(state, "BENCH", trades[i], float(1002 + i))

    duration = time.perf_counter() - start
    ops_sec = N / duration

    print(f"Symbols Tracked: {len(state.trades)}")  # Latest trade per symbol.
    print(f"Events Recorded: {len(state.events)}")
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} trades processed/sec")


if __name__ == "__main__":
    run_benchmark()
