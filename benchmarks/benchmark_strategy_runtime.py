"""Performance benchmark for the Strategy Runtime event dispatcher."""

import time
from collections.abc import Iterable
from decimal import Decimal

from alphalab.strategy import (
    BaseStrategy,
    FillEvent,
    Intent,
    StrategyContext,
    StrategyEngine,
)
from tests.unit.strategy.test_strategy import DummyContextBuilder, setup_running_strategy


class FastStrategy(BaseStrategy):
    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        return (Intent("S1", event.instrument, event.fill_quantity),)


def run_benchmark() -> None:
    runtime = setup_running_strategy("S1", FastStrategy())
    ctx_builder = DummyContextBuilder()

    event = FillEvent(
        event_id="E1",
        timestamp=100.0,
        order_id="O1",
        instrument="AAPL",
        fill_quantity=Decimal("10"),
        fill_price=Decimal("150"),
    )

    N = 100_000
    print(f"Starting Strategy Runtime Dispatch Benchmark: {N} hooks...")

    start = time.perf_counter()
    for i in range(N):
        runtime, _ = StrategyEngine.process_event(
            state=runtime,
            event=event,
            context_factory=ctx_builder.build,
            timestamp=float(i),
        )
    duration = time.perf_counter() - start

    ops_sec = N / duration
    print(f"Dispatch Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} hook invocations/sec")


if __name__ == "__main__":
    run_benchmark()
