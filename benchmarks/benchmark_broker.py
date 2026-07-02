"""High-performance benchmark suite for the Broker Abstraction Layer."""

import time
from dataclasses import dataclass
from decimal import Decimal

from alphalab.broker import (
    BrokerAdapter,
    BrokerEngine,
    BrokerOrderType,
    PaperBroker,
)


@dataclass(frozen=True)
class MockOMSOrder:
    order_id: str
    asset_id: str
    side: str
    quantity: str
    price: str


def run_benchmark() -> None:
    state = BrokerEngine.initialize("PAPER-BENCH", Decimal("100000000.00"))
    broker = PaperBroker()
    
    N = 100_000
    print(f"Starting Broker Benchmark: Submitting {N} Market Orders...")

    # 1. Prepare 100,000 OMS mock orders
    oms_orders = tuple(
        MockOMSOrder(f"OMS-{i}", "AAPL", "BUY", "10", "150.00") for i in range(N)
    )

    start = time.perf_counter()

    # 2. Sequentially translate and process each order
    for i, oms_order in enumerate(oms_orders):
        broker_order = BrokerAdapter.to_broker_order(
            oms_order, f"B-{i}", BrokerOrderType.MARKET, float(i)
        )
        state, _ = broker.submit_order(state, broker_order, float(i))

    duration = time.perf_counter() - start

    ops_sec = N / duration
    print(f"PaperBroker Order Execution Time: {duration:.4f}s")
    print(f"Executions Processed: {len(state.executions)}")
    print(f"Throughput: {ops_sec:.2f} orders/sec")


if __name__ == "__main__":
    run_benchmark()