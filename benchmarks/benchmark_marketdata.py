"""High-performance benchmarking suite for the functional Market Data Engine."""

import time

from alphalab.marketdata import MarketDataEngine, ProviderConfig, Trade
from alphalab.marketdata.yahoo.adapter import YahooAdapter
from alphalab.marketdata.yahoo.config import YahooConfig


def run_benchmark() -> None:
    N = 100_000
    print(f"Starting Market Data Infrastructure Benchmark: Normalizing {N} Trades...")

    state = MarketDataEngine.initialize("MD-BENCH")
    cfg = ProviderConfig("BENCH-YAHOO", "Yahoo", "bench")
    provider = YahooAdapter(YahooConfig("BENCH-YAHOO", "bench"))

    state = MarketDataEngine.register(state, cfg, 1000.0)
    state = MarketDataEngine.connect(state, "BENCH-YAHOO", provider, 1001.0)

    # Pre-generate generic AlphaLab dict payloads
    trades = tuple(
        Trade("AAPL", float(1002 + i), 150.0 + (i % 10), 100.0)
        for i in range(N)
    )

    start = time.perf_counter()

    for i in range(N):
        state = MarketDataEngine.process_trade(state, "BENCH-YAHOO", trades[i], float(1002 + i))

    duration = time.perf_counter() - start
    ops_sec = N / duration
    
    print(f"Total Ticks Processed: {len(state.trades)}") # Tracks latest per symbol.
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} trades normalized/sec")

if __name__ == "__main__":
    run_benchmark()