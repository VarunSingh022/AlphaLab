"""High-performance benchmarking suite for the functional immutable OMS Engine.

The 100k workload below was not meaningfully runnable before v2.2. ``OrderBook``
copied its whole order ``dict`` on every add and replace, and ``OMSEngine``
rebuilt both order-id ``frozenset`` s on every store, so the cost was quadratic
in the number of orders: measured on the development machine, doubling the
workload cost ~4.4x the time, and this benchmark's 100k stages took ~16 minutes.

v2.2 moved the book and the active/completed sets onto
:class:`~alphalab.common.persistent_map.PersistentMap` /
:class:`~alphalab.common.persistent_map.PersistentSet`, which share structure
instead of copying. The same workload now finishes in a few seconds.

The scaling check the benchmark opens with is its real point: it runs the full
workload at two sizes and reports the growth factor, which is what distinguishes
a linear structure from a quadratic one regardless of machine speed.
"""

import gc
import time
from decimal import Decimal

from alphalab.oms import OMSEngine, OMSState, Order, OrderId, OrderStatus, OrderType, Side

N = 100_000
#: Linear growth predicts 2.0 across a doubling; quadratic predicts ~4.0.
MAX_SCALING_FACTOR = 3.0


def _orders(count: int, base_ts: float) -> list[Order]:
    return [
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
        for _ in range(count)
    ]


def _full_workload(count: int, base_ts: float) -> float:
    """Submit, cancel half, accept-and-fill half. Returns elapsed seconds.

    The cyclic collector is paused around the timed section. Every order,
    event and state here is a container object, so a run keeps a large live
    heap; with the collector on, what the timing measures is mostly how often
    CPython walked that heap, which swamps the structure being compared. (Left
    on, a 2x workload has been measured at anywhere from 1.3x to 5.2x on the
    same build, in both directions -- the number stops meaning anything.)
    """

    orders = _orders(count, base_ts)
    state = OMSState()
    half = count // 2

    gc.disable()
    try:
        start = time.perf_counter()
        for order in orders:
            state = OMSEngine.submit(state, order, base_ts)
        for order in orders[half:]:
            state = OMSEngine.cancel(state, order.order_id, base_ts + 1)
        for order in orders[:half]:
            state = OMSEngine.accept(state, order.order_id, base_ts + 1)
            state = OMSEngine.fill(
                state, order.order_id, Decimal("100.0"), Decimal("150.0"), base_ts + 2
            )
        return time.perf_counter() - start
    finally:
        gc.enable()


def run_benchmark() -> None:
    base_ts = time.time()

    # Scaling first, on a clean heap: the 100k stages below retain a large
    # live state, and measuring growth alongside it measures the collector.
    small = _full_workload(10_000, base_ts)
    large = _full_workload(20_000, base_ts)
    scaling = large / max(small, 1e-9)
    print(
        f"Scaling: 10k -> 20k order lifecycles cost {scaling:.2f}x the time "
        f"({small:.3f}s -> {large:.3f}s; linear 2.00x, quadratic ~4.00x)"
    )

    orders_to_add = _orders(N, base_ts)
    state = OMSState()

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

    print(f"Total: {sub_time + cancel_time + fill_time:.4f}s for {N} order lifecycles")
    print(f"Book size: {len(state.orders.orders())} orders, {len(state.history)} events")

    if scaling > MAX_SCALING_FACTOR:
        raise SystemExit(
            f"OMS scaling factor {scaling:.2f}x exceeds {MAX_SCALING_FACTOR:.2f}x; "
            "the order book has regressed toward quadratic behaviour."
        )


if __name__ == "__main__":
    run_benchmark()
