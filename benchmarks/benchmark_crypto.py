"""High-performance benchmark suite for the stateless Crypto Engine."""

import time
from decimal import Decimal

from alphalab.crypto import (
    CryptoInstrument,
    FundingRate,
    FundingRateHistory,
    InstrumentType,
    annualized_funding_rate,
    compute_funding_payment,
    compute_liquidation_price,
    open_crypto_position,
    parse_exchange_symbol,
    to_exchange_symbol,
)
from alphalab.portfolio.types import PositionSide


def run_benchmark() -> None:
    instrument = CryptoInstrument(
        base_asset="BTC",
        quote_asset="USDT",
        instrument_type=InstrumentType.PERPETUAL,
        exchange="binance",
    )
    history = FundingRateHistory(
        instrument_symbol="X",
        rates=tuple(
            FundingRate(instrument_symbol="X", rate=Decimal("0.0001"), timestamp=float(i * 28800))
            for i in range(30)
        ),
    )

    N = 100_000
    print(f"Starting Crypto Engine Benchmark: {N} computations per operation...")

    start = time.perf_counter()
    for _ in range(N):
        open_crypto_position(instrument, Decimal("1"), Decimal("50000.00"), timestamp=1000.0)
    duration = time.perf_counter() - start
    print(f"  open_crypto_position   : {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        compute_funding_payment(Decimal("1"), Decimal("50000"), Decimal("0.0001"))
    duration = time.perf_counter() - start
    print(f"  compute_funding_payment: {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        compute_liquidation_price(
            Decimal("50000"), PositionSide.LONG, Decimal("10"), Decimal("0.005")
        )
    duration = time.perf_counter() - start
    print(f"  compute_liquidation_price: {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        symbol = to_exchange_symbol("kraken", "BTC", "USD")
        parse_exchange_symbol("kraken", symbol)
    duration = time.perf_counter() - start
    print(f"  symbol round-trip      : {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        annualized_funding_rate(history)
    duration = time.perf_counter() - start
    print(f"  annualized_funding_rate: {duration:.4f}s total, {N / duration:.2f} ops/sec")


if __name__ == "__main__":
    run_benchmark()
