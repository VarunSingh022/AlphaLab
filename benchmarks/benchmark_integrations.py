"""High-performance benchmarking suite for the functional Integration Engine."""

import time

from alphalab.integrations import BrokerConfig, IntegrationAdapter, IntegrationEngine
from alphalab.integrations.paper import PaperAdapter
from alphalab.integrations.paper.config import PaperConfig


def run_benchmark() -> None:
    N = 100_000
    print(f"Starting Integration Framework Benchmark: Routing {N} Orders...")

    state = IntegrationEngine.initialize("INT-BENCH")
    cfg = BrokerConfig("BENCH-PAPER", "PaperBroker", "bench", "local")
    provider = PaperAdapter(PaperConfig(api_key="bench", api_secret="bench"))

    state = IntegrationEngine.register(state, cfg)
    state = IntegrationEngine.authenticate(state, "BENCH-PAPER", provider, {}, 1000.0)
    state = IntegrationEngine.connect(state, "BENCH-PAPER", provider, 1001.0)

    # Pre-generate generic AlphaLab dict payloads
    orders = tuple(
        IntegrationAdapter.to_broker_payload({
            "order_id": f"O-{i}",
            "symbol": "AAPL",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 10.0,
            "price": 0.0,
        })
        for i in range(N)
    )

    start = time.perf_counter()

    for i in range(N):
        state = IntegrationEngine.submit_order(
            state, "BENCH-PAPER", provider, orders[i], float(1002 + i)
        )

    duration = time.perf_counter() - start
    ops_sec = N / duration

    print(f"Total Integrations Processed: {state.metrics.orders_submitted}")
    print(f"Total Execution Feedbacks: {state.metrics.executions_processed}")
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} orders routed/sec")


if __name__ == "__main__":
    run_benchmark()
