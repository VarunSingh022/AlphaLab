"""High-performance benchmark suite for the stateless Options Engine."""

import time
from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.options import (
    OptionContract,
    OptionLeg,
    OptionStrategy,
    OptionType,
    black_scholes_greeks,
    black_scholes_price,
    compute_payoff_at_expiry,
)

ONE_YEAR = 365.25 * 86400


def run_benchmark() -> None:
    contract = OptionContract(
        underlying_asset_id="AAPL",
        strike=Decimal("150.00"),
        expiry=ONE_YEAR,
        option_type=OptionType.CALL,
    )
    strategy = OptionStrategy(
        legs=(
            OptionLeg(contract=contract, side=Side.BUY, quantity=1),
            OptionLeg(
                contract=OptionContract(
                    underlying_asset_id="AAPL",
                    strike=Decimal("160.00"),
                    expiry=ONE_YEAR,
                    option_type=OptionType.CALL,
                ),
                side=Side.SELL,
                quantity=1,
            ),
        )
    )

    N = 100_000
    print(f"Starting Options Engine Benchmark: {N} computations per operation...")

    start = time.perf_counter()
    for _ in range(N):
        black_scholes_price(contract, Decimal("155.00"), 0.25, 0.05, 0.0)
    duration = time.perf_counter() - start
    print(f"  black_scholes_price : {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        black_scholes_greeks(contract, Decimal("155.00"), 0.25, 0.05, 0.0)
    duration = time.perf_counter() - start
    print(f"  black_scholes_greeks: {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        compute_payoff_at_expiry(strategy, Decimal("165.00"))
    duration = time.perf_counter() - start
    print(f"  strategy_payoff     : {duration:.4f}s total, {N / duration:.2f} ops/sec")


if __name__ == "__main__":
    run_benchmark()
