"""High-performance benchmark suite for the stateless Futures Engine."""

import time
from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.futures import (
    AdjustmentMethod,
    CalendarSpread,
    CalendarSpreadLeg,
    FutureContract,
    FuturesCurve,
    FuturesCurvePoint,
    RollSegment,
    build_continuous_series,
    compute_spread_value,
    curve_slope,
)
from alphalab.market.bar import Bar, TimeFrame


def _contract(month_offset_days: int) -> FutureContract:
    return FutureContract(
        underlying_asset_id="CL",
        contract_month=float(month_offset_days * 86400),
        expiry=float((month_offset_days + 30) * 86400),
        multiplier=1000,
        tick_size=Decimal("0.01"),
    )


def _bar(day: int, close: Decimal) -> Bar:
    return Bar(
        asset_id="CL",
        timestamp=float(day * 86400),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("100"),
        vwap=close,
        trade_count=10,
        timeframe=TimeFrame.D1,
    )


def run_benchmark() -> None:
    dec = _contract(0)
    jan = _contract(30)
    feb = _contract(60)

    segments = (
        RollSegment(
            contract=dec,
            bars=tuple(_bar(i, Decimal("50") + i) for i in range(20)),
            outgoing_roll_price=Decimal("70"),
            incoming_roll_price=Decimal("71"),
        ),
        RollSegment(
            contract=jan,
            bars=tuple(_bar(i, Decimal("52") + i) for i in range(20, 40)),
            outgoing_roll_price=Decimal("72"),
            incoming_roll_price=Decimal("74"),
        ),
        RollSegment(contract=feb, bars=tuple(_bar(i, Decimal("55") + i) for i in range(40, 60))),
    )

    spread = CalendarSpread(
        legs=(
            CalendarSpreadLeg(contract=dec, side=Side.BUY, quantity=1),
            CalendarSpreadLeg(contract=jan, side=Side.SELL, quantity=1),
        )
    )
    prices = {"CL_197001": Decimal("70.00"), "CL_197002": Decimal("75.00")}

    curve = FuturesCurve(
        underlying_asset_id="CL",
        timestamp=0.0,
        points=(
            FuturesCurvePoint(contract_month=0.0, price=Decimal("70.00")),
            FuturesCurvePoint(contract_month=2592000.0, price=Decimal("72.00")),
        ),
    )

    N = 100_000
    print(f"Starting Futures Engine Benchmark: {N} computations per operation...")

    start = time.perf_counter()
    for _ in range(N):
        build_continuous_series(segments, AdjustmentMethod.BACK_ADJUSTED)
    duration = time.perf_counter() - start
    print(f"  build_continuous_series (60 bars): {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        compute_spread_value(spread, prices)
    duration = time.perf_counter() - start
    print(f"  compute_spread_value             : {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        curve_slope(curve)
    duration = time.perf_counter() - start
    print(f"  curve_slope                      : {duration:.4f}s total, {N / duration:.2f} ops/sec")


if __name__ == "__main__":
    run_benchmark()
