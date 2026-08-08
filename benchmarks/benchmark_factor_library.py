"""High-performance benchmark suite for the stateless Factor Library."""

import time
from collections.abc import Callable
from decimal import Decimal

from alphalab.factor_library import (
    FactorResult,
    FundamentalSnapshot,
    PriceSeries,
    compute_carry,
    compute_momentum,
    compute_quality,
    compute_value,
    compute_volatility,
)
from alphalab.market.bar import Bar, TimeFrame


def _build_price_series(days: int = 252) -> PriceSeries:
    bars = tuple(
        Bar(
            asset_id="AAPL",
            timestamp=float(i * 86400),
            open=Decimal("100") + Decimal(i % 10),
            high=Decimal("101") + Decimal(i % 10),
            low=Decimal("99") + Decimal(i % 10),
            close=Decimal("100") + Decimal(i % 10),
            volume=Decimal("1000000"),
            vwap=Decimal("100") + Decimal(i % 10),
            trade_count=5000,
            timeframe=TimeFrame.D1,
        )
        for i in range(days)
    )
    return PriceSeries(asset_id="AAPL", bars=bars)


def run_benchmark() -> None:
    prices = _build_price_series()
    fundamentals = FundamentalSnapshot(
        asset_id="AAPL",
        timestamp=0.0,
        price=Decimal("150.00"),
        earnings_per_share=Decimal("6.00"),
        book_value_per_share=Decimal("30.00"),
        dividend_per_share=Decimal("3.00"),
    )

    N = 100_000
    print(f"Starting Factor Library Benchmark: {N} computations per factor...")

    benchmarks: tuple[tuple[str, Callable[[], FactorResult]], ...] = (
        ("momentum", lambda: compute_momentum(prices, "momentum_20d", 1, 20, 0.0)),
        ("volatility", lambda: compute_volatility(prices, "vol_20d", 1, 20, 0.0)),
        ("value", lambda: compute_value(fundamentals, "value_ey", 1, 0.0)),
        ("quality", lambda: compute_quality(fundamentals, "quality_roe", 1, 0.0)),
        ("carry", lambda: compute_carry(fundamentals, "carry_dy", 1, 0.0)),
    )
    for name, fn in benchmarks:
        start = time.perf_counter()
        for _ in range(N):
            fn()
        duration = time.perf_counter() - start
        ops_sec = N / duration
        print(f"  {name:12s}: {duration:.4f}s total, {ops_sec:.2f} computations/sec")


if __name__ == "__main__":
    run_benchmark()
