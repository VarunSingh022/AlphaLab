"""High-performance benchmark suite for the stateless Macro Engine."""

import time
from decimal import Decimal

from alphalab.macro import (
    IndicatorObservation,
    YieldCurve,
    YieldCurvePoint,
    is_inverted,
    known_as_of,
    real_interest_rate_exact,
    two_year_ten_year_spread,
)

DAY = 86400.0


def run_benchmark() -> None:
    observations = tuple(
        IndicatorObservation(
            indicator_id="GDP",
            reference_period=float(i) * 30 * DAY,
            release_date=float(i) * 30 * DAY + 31 * DAY,
            value=Decimal("100") + i,
        )
        for i in range(60)
    )
    curve = YieldCurve(
        country="US",
        currency="USD",
        timestamp=0.0,
        points=(
            YieldCurvePoint(tenor_years=Decimal("0.25"), yield_rate=Decimal("0.052")),
            YieldCurvePoint(tenor_years=Decimal("2"), yield_rate=Decimal("0.048")),
            YieldCurvePoint(tenor_years=Decimal("10"), yield_rate=Decimal("0.042")),
        ),
    )

    N = 100_000
    print(f"Starting Macro Engine Benchmark: {N} computations per operation...")

    start = time.perf_counter()
    for _ in range(N):
        known_as_of(observations, as_of=1000.0 * DAY)
    duration = time.perf_counter() - start
    print(f"  known_as_of (60 obs)  : {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        two_year_ten_year_spread(curve)
    duration = time.perf_counter() - start
    print(f"  two_year_ten_year_spread: {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        is_inverted(curve)
    duration = time.perf_counter() - start
    print(f"  is_inverted           : {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        real_interest_rate_exact(Decimal("0.05"), Decimal("0.03"))
    duration = time.perf_counter() - start
    print(f"  real_interest_rate_exact: {duration:.4f}s total, {N / duration:.2f} ops/sec")


if __name__ == "__main__":
    run_benchmark()
