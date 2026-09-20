"""Benchmark suite for market conventions: settlement, ticks, lots and notionals."""

import time
from datetime import date, timedelta
from datetime import time as clock
from decimal import Decimal

from alphalab.conventions import (
    Compounding,
    DayCount,
    LotSpecification,
    MarketConvention,
    RoundingDirection,
    SettlementBasis,
    SettlementRule,
    TickBand,
    TickSchedule,
    contract_notional,
    discount_factor,
    round_to_tick,
    settlement_date,
    year_fraction,
)
from alphalab.data.calendar import MarketCalendar, SessionWindow

_CALENDAR = MarketCalendar(
    calendar_id="XNSE",
    timezone_name="Asia/Kolkata",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(clock(9, 15), clock(15, 30)),)),
    holidays=frozenset(),
)

_FLAT = TickSchedule.flat(Decimal("0.01"))
_TIERED = TickSchedule(
    (
        TickBand(Decimal("100"), Decimal("0.01")),
        TickBand(Decimal("1000"), Decimal("0.05")),
        TickBand(None, Decimal("0.10")),
    )
)
_CONVENTION = MarketConvention(
    venue="XCME",
    calendar_id="XCME",
    quote_currency="USD",
    settlement_currency="USD",
    multiplier=Decimal("1000"),
    tick=_FLAT,
    lot=LotSpecification.single_units(),
    settlement=SettlementRule(SettlementBasis.TRADING_DAYS, 2),
)


def run_benchmark() -> None:
    n = 100_000
    print(f"Starting Market Conventions Benchmark: {n} computations per operation...")

    trade_date = date(2026, 3, 13)
    rule = SettlementRule(SettlementBasis.TRADING_DAYS, 2)

    start = time.perf_counter()
    for _ in range(n):
        settlement_date(rule, trade_date, _CALENDAR)
    duration = time.perf_counter() - start
    print(f"  settlement_date (T+2)  : {duration:.4f}s total, {n / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(n):
        _FLAT.tick_size_at(Decimal("750.25"))
    duration = time.perf_counter() - start
    print(f"  tick_size_at (flat)    : {duration:.4f}s total, {n / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(n):
        _TIERED.tick_size_at(Decimal("750.25"))
    duration = time.perf_counter() - start
    print(f"  tick_size_at (3 bands) : {duration:.4f}s total, {n / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(n):
        _CONVENTION.tick_value_at(Decimal("750.25"))
    duration = time.perf_counter() - start
    print(f"  tick_value_at          : {duration:.4f}s total, {n / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(n):
        round_to_tick(Decimal("750.234"), Decimal("0.05"), RoundingDirection.NEAREST)
    duration = time.perf_counter() - start
    print(f"  round_to_tick          : {duration:.4f}s total, {n / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(n):
        contract_notional(_CONVENTION, Decimal("10"), Decimal("75.50"))
    duration = time.perf_counter() - start
    print(f"  contract_notional      : {duration:.4f}s total, {n / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(n):
        year_fraction(date(2025, 1, 31), date(2025, 7, 31), DayCount.THIRTY_360_US)
    duration = time.perf_counter() - start
    print(f"  year_fraction (30/360) : {duration:.4f}s total, {n / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(n):
        discount_factor(0.05, 10.0, Compounding.SEMI_ANNUAL)
    duration = time.perf_counter() - start
    print(f"  discount_factor        : {duration:.4f}s total, {n / duration:.2f} ops/sec")

    print("\nCalendar session scaling (holidays -> per-lookup cost):")
    instant = 1_773_669_600.0
    for holidays in (0, 100, 1_000):
        calendar = MarketCalendar(
            calendar_id="X",
            timezone_name="UTC",
            weekly_sessions=dict.fromkeys(range(5), (SessionWindow(clock(9, 30), clock(16)),)),
            holidays=frozenset(
                date(2000, 1, 1) + timedelta(days=index * 7) for index in range(holidays)
            ),
        )
        start = time.perf_counter()
        for _ in range(20_000):
            calendar.is_open(instant)
        duration = time.perf_counter() - start
        per_call = duration / 20_000 * 1e6
        print(f"  {holidays:>5} holidays        : {duration:.4f}s / 20,000 = {per_call:.3f} us")


if __name__ == "__main__":
    run_benchmark()
