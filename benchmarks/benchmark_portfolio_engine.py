import time
from decimal import Decimal

from alphalab.portfolio import (
    Account,
    NAVCalculator,
    PortfolioEngine,
    PortfolioState,
)


def run_benchmark() -> None:
    account = Account(
        account_id="BENCH",
        base_currency="USD",
        name="Perf Test",
        created_at=0.0,
    )

    state = PortfolioState(account=account)
    state = PortfolioEngine.apply_deposit(
        state,
        Decimal("10000000.00"),
        "USD",
        0.0,
    )

    N = 100_000
    qty = Decimal("1")
    price = Decimal("100.00")
    comm = Decimal("0.50")

    print(f"Starting benchmark: {N} fills...")

    start_time = time.perf_counter()

    for i in range(N):
        state = PortfolioEngine.apply_fill(
            state,
            "AAPL",
            qty,
            price,
            comm,
            float(i),
        )

        if i % 2 == 0:
            state = PortfolioEngine.apply_fill(
                state,
                "AAPL",
                -qty,
                price,
                comm,
                float(i),
            )

    end_time = time.perf_counter()

    duration = end_time - start_time
    ops_per_sec = N / duration

    nav_start = time.perf_counter()
    _ = NAVCalculator.calculate(state.cash, state.positions)
    nav_end = time.perf_counter()

    print(f"Fills complete. Operations/sec: {ops_per_sec:.2f}")
    print(f"Total time for {N} ops: {duration:.4f} seconds")
    print(f"NAV Calculation time: {(nav_end - nav_start) * 1000:.4f} ms")


if __name__ == "__main__":
    run_benchmark()
