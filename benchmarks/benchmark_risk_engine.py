"""High-performance benchmark suite for the functional Risk Engine."""

import time
from dataclasses import replace
from decimal import Decimal

from alphalab.risk import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderRequest,
    OrderSide,
    OrderSizeLimit,
    PositionLimit,
    RiskEngine,
    RiskLimits,
)


def run_benchmark() -> None:
    limits = RiskLimits(
        order_size=OrderSizeLimit(Decimal("1000"), Decimal("100000")),
        position=PositionLimit(Decimal("5000"), Decimal("500000")),
        exposure=ExposureLimit(Decimal("1000000"), Decimal("500000")),
        leverage=LeverageLimit(Decimal("2.0")),
        margin=MarginLimit(Decimal("0.80")),
        daily_loss=DailyLossLimit(Decimal("10000")),
        drawdown=DrawdownLimit(Decimal("0.10")),
    )

    state = RiskEngine.reset(limits)

    # Bypass frozen constraints purely for providing synthetic test liquidity
    state = replace(
        state,
        buying_power=Decimal("1000000"),
    )

    request = OrderRequest(
        order_id="BENCH-ORD",
        strategy_id="STRAT-01",
        asset_id="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("150.00"),
    )

    N = 100_000
    print(f"Starting Risk Engine Benchmark: {N} evaluations...")

    start = time.perf_counter()
    for i in range(N):
        state, _ = RiskEngine.evaluate(state, request, float(i))
    duration = time.perf_counter() - start

    ops_sec = N / duration
    print(f"Risk Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} evaluations/sec")
    print(f"Total risk events emitted: {len(state.events)}")


if __name__ == "__main__":
    run_benchmark()
