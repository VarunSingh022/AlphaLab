"""High-performance benchmarking suite for the functional Broker Connector Framework."""

import time
from decimal import Decimal

from alphalab.brokers import (
    AccountSnapshot,
    BrokerAdapter,
    BrokerConnection,
    BrokerConnectorEngine,
    BrokerType,
    ExecutionReport,
)


def create_mock_payload(oid: str) -> dict[str, str | float | Decimal]:
    return {
        "order_id": oid,
        "account_id": "BENCH-ACC",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "tif": "IOC",
        "quantity": Decimal("10"),
        "price": Decimal("150.00"),
        "stop_price": Decimal("0.0"),
        "timestamp": 1000.0,
    }


def run_benchmark() -> None:
    # 1. Setup Infrastructure
    state = BrokerConnectorEngine.initialize("BENCH-ENG-01")
    conn = BrokerConnection("B-1", "BenchBroker", BrokerType.PAPER)
    acc = AccountSnapshot(
        account_id="BENCH-ACC",
        cash=Decimal("10000000.00"),
        equity=Decimal("10000000.00"),
        buying_power=Decimal("10000000.00"),
        margin=Decimal("0.00"),
        available_funds=Decimal("10000000.00"),
        currency="USD",
        broker_id="B-1",
    )

    state = BrokerConnectorEngine.register_broker(state, conn, 1000.0)
    state = BrokerConnectorEngine.connect_broker(state, "B-1", 1001.0)
    state = BrokerConnectorEngine.add_account(state, acc)

    N = 10_000
    print(f"Starting Broker Framework Benchmark: Submitting and Settling {N} Orders...")

    # Pre-generate objects
    orders = tuple(BrokerAdapter.dict_to_order(create_mock_payload(f"O-{i}")) for i in range(N))
    executions = tuple(
        ExecutionReport(
            execution_id=f"E-{i}",
            broker_order_id=f"O-{i}",
            symbol="AAPL",
            fill_quantity=Decimal("10"),
            fill_price=Decimal("150.00"),
            commission=Decimal("0.50"),
            timestamp=float(1002 + i),
            account_id="BENCH-ACC",
        )
        for i in range(N)
    )

    # 2. Benchmark Loop
    start = time.perf_counter()

    for i in range(N):
        state = BrokerConnectorEngine.submit_order(state, orders[i], float(1001 + i))
        state = BrokerConnectorEngine.process_execution(state, executions[i], float(1002 + i))

    duration = time.perf_counter() - start

    ops_sec = N / duration
    print(f"Total Processing Time: {duration:.4f}s")
    print(f"Orders Submitted: {state.statistics.total_orders_submitted}")
    print(f"Executions Settled: {state.statistics.total_executions_received}")
    print(f"Throughput: {ops_sec:.2f} order-execution cycles/sec")


if __name__ == "__main__":
    run_benchmark()
