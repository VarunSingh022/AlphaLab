"""High-performance benchmarking suite for the functional immutable OMS Engine."""

import sys
import time
from decimal import Decimal

from alphalab.oms import OMSEngine, OMSState, Order, OrderId, OrderStatus, OrderType, Side


def run_benchmark() -> None:
    state = OMSState()
    N = 100_000

    base_ts = time.time()
    orders_to_add: list[Order] = [
        Order(
            order_id=OrderId.generate(),
            strategy_id="BENCH",
            asset_id="AAPL",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.NEW,
            quantity=Decimal("100.0"),
            filled_quantity=Decimal("0.0"),
            remaining_quantity=Decimal("100.0"),
            limit_price=Decimal("150.0"),
            stop_price=None,
            average_fill_price=Decimal("0.0"),
            created_at=base_ts,
            updated_at=base_ts,
        )
        for _ in range(N)
    ]

    print(f"Starting Benchmark: {N} operations per stage.")

    # 1. Submission
    start = time.perf_counter()
    for o in orders_to_add:
        state = OMSEngine.submit(state, o, base_ts)
    sub_time = time.perf_counter() - start
    print(f"Submissions: {N / sub_time:.2f} ops/sec")

    # 2. Lookups
    start = time.perf_counter()
    for o in orders_to_add:
        _ = state.orders.find(o.order_id)
    lookup_time = time.perf_counter() - start
    print(f"Lookups: {N / lookup_time:.2f} ops/sec | Latency: {(lookup_time / N) * 1e6:.2f} µs/op")

    # 3. Transitions & Fills (We will fill half, cancel half)
    half = N // 2
    fill_orders = orders_to_add[:half]
    cancel_orders = orders_to_add[half:]

    start = time.perf_counter()
    for o in cancel_orders:
        state = OMSEngine.cancel(state, o.order_id, base_ts + 1)
    cancel_time = time.perf_counter() - start
    print(f"Cancellations: {half / cancel_time:.2f} ops/sec")

    start = time.perf_counter()
    for o in fill_orders:
        state = OMSEngine.accept(state, o.order_id, base_ts + 1)
        state = OMSEngine.fill(state, o.order_id, Decimal("100.0"), Decimal("150.0"), base_ts + 2)
    fill_time = time.perf_counter() - start
    print(f"Accept & Fill Pair: {half / fill_time:.2f} sequence/sec")

    # Memory Size Estimate
    mem_size_mb = sys.getsizeof(state.orders._orders) / (1024 * 1024)
    print(f"Approx Shallow Index Size: {mem_size_mb:.2f} MB")


if __name__ == "__main__":
    run_benchmark()
