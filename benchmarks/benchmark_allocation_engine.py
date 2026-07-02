"""High-performance benchmarking suite for the functional Allocation Engine."""

import time
from decimal import Decimal

from alphalab.allocation import (
    AllocationConstraints,
    AllocationEngine,
    CapitalBudget,
    FixedQuantitySizing,
)
from alphalab.strategy.events import Intent


def run_benchmark() -> None:
    budget = CapitalBudget(
        global_capital=Decimal("100000000.00"),
        maximum_exposure=Decimal("200000000.00"),
    )
    state = AllocationEngine.initialize(budget)
    sizer = FixedQuantitySizing()
    constraints = AllocationConstraints()

    N = 1000

    # Construct 1000 alternating buy/sell intents to force heavy netting
    intents = tuple(
        Intent(
            strategy_id=f"STRAT-{i % 5}",
            instrument="AAPL",
            target=Decimal("10") if i % 2 == 0 else Decimal("-5"),
            correlation_id=f"INT-{i}",
        )
        for i in range(N)
    )

    prices = {"AAPL": Decimal("150.00")}

    print(f"Starting Allocation Engine Benchmark: {N} Intents Batch...")

    start = time.perf_counter()
    _unused_state, orders = AllocationEngine.allocate(
        state=state,
        intents=intents,
        market_prices=prices,
        sizing_model=sizer,
        constraints=constraints,
        timestamp=1000.0,
    )
    duration = time.perf_counter() - start

    # 1000 intents of (+10) and (-5).
    # 500 buys * 10 = +5000. 500 sells * -5 = -2500.
    # Net = +2500 AAPL. Resulting in EXACTLY 1 OrderRequest.

    print(f"Allocation & Netting Time: {duration:.4f}s")
    print(f"Intents Processed: {N}")
    print(f"Output Netted Orders: {len(orders)}")
    print(f"Throughput: {N / duration:.2f} intents/sec")


if __name__ == "__main__":
    run_benchmark()
