"""Performance benchmark for the deterministic Execution Engine."""

import time
from decimal import Decimal

from alphalab.execution import (
    ExecutionEngine,
    ExecutionSimulator,
    ExecutionState,
    FillStatus,
    OrderInstruction,
)


def run_benchmark() -> None:
    state = ExecutionState()
    simulator = ExecutionSimulator()
    N = 100_000

    instruction = OrderInstruction(
        order_id="BENCH-1",
        strategy_id="STRAT",
        asset_id="AAPL",
        quantity=Decimal("100"),
        price=Decimal("150.00"),
        side="BUY",
        venue="SIM",
        currency="USD",
    )

    print(f"Starting Execution Engine Benchmark: {N} fills...")

    start_time = time.perf_counter()
    for i in range(N):
        state = ExecutionEngine.simulate(
            state=state,
            simulator=simulator,
            instruction=instruction,
            fill_quantity=Decimal("1"),
            market_price=Decimal("150.00"),
            timestamp=float(i),
            status=FillStatus.PARTIAL_FILL,
        )
    end_time = time.perf_counter()

    duration = end_time - start_time
    ops_sec = N / duration

    print(f"Execution Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} operations/sec")
    print(f"Total reports generated: {len(state.reports)}")


if __name__ == "__main__":
    run_benchmark()
