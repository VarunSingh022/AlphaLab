"""Benchmark suite for the v3.4 multi-asset research paths.

Reports **measured scaling**, not a claimed complexity class: each path is run at
several input sizes and the ratio between them is printed beside the linear
prediction, so a super-linear term shows up as a ratio well above it.
``tests/regression/test_v34_complexity.py`` holds the same measurements as
standing assertions.
"""

import time
from datetime import UTC, date, datetime
from decimal import Decimal

from alphalab.conventions import (
    Compounding,
    DayCount,
    LotSpecification,
    MarketConvention,
    SettlementBasis,
    SettlementRule,
    TickSchedule,
)
from alphalab.crypto import (
    FeeSchedule,
    FundingRate,
    FundingRateHistory,
    LiquidityRole,
    PriceSource,
    VenueSpecification,
    accrued_funding,
    coverage,
    cross_venue_dispersion,
    trading_fee,
)
from alphalab.futures import (
    AdjustmentMethod,
    ContractChain,
    FutureContract,
    RollPolicy,
    RollTrigger,
    build_continuous_series,
    continuous_segments,
    curve_shape,
    futures_symbol,
    roll_schedule,
)
from alphalab.futures.curve import FuturesCurve, FuturesCurvePoint
from alphalab.macro.bond import Bond, clean_price, convexity, modified_duration
from alphalab.macro.bond import yield_from_clean_price as invert_yield
from alphalab.market.bar import Bar, TimeFrame
from alphalab.options import (
    ExerciseStyle,
    OptionChain,
    OptionContract,
    OptionType,
    black_scholes_greeks,
    black_scholes_value,
    implied_volatility,
    occ_symbol,
    surface_from_chain,
)
from alphalab.portfolio.contracts import ContractHolding, contract_exposures
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.portfolio.fx_research import ForwardTerms, covered_forward_rate
from alphalab.portfolio.position import Position

DAY = 86400.0
YEAR = 365.25 * DAY


def _timed(label: str, work: object, repeats: int) -> float:
    start = time.perf_counter()
    for _ in range(repeats):
        work()  # type: ignore[operator]
    duration = time.perf_counter() - start
    print(f"  {label:<34}: {duration:.4f}s total, {repeats / duration:>12,.0f} ops/sec")
    return duration


def _scaling(label: str, sizes: tuple[int, ...], build: object) -> None:
    print(f"\n  {label} (measured scaling)")
    previous: tuple[int, float] | None = None
    for size in sizes:
        work = build(size)  # type: ignore[operator]
        best = min(_elapsed(work) for _ in range(3))
        if previous is None:
            print(f"    {size:>8,} -> {best * 1000:>9.3f} ms")
        else:
            prior_size, prior_time = previous
            factor = size / prior_size
            observed = best / max(prior_time, 1e-9)
            print(
                f"    {size:>8,} -> {best * 1000:>9.3f} ms   "
                f"{observed:>6.2f}x for {factor:.0f}x input (linear = {factor:.0f}x)"
            )
        previous = (size, best)


def _elapsed(work: object) -> float:
    start = time.perf_counter()
    work()  # type: ignore[operator]
    return time.perf_counter() - start


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _contract(index: int) -> FutureContract:
    stamp = datetime(2026 + index // 12, index % 12 + 1, 1, tzinfo=UTC).timestamp()
    return FutureContract("CL", stamp, stamp, 1000, Decimal("0.01"), "USD")


def _bars(symbol: str, count: int, lead_from: int) -> tuple[Bar, ...]:
    price = Decimal("70.00")
    return tuple(
        Bar(
            symbol,
            step * DAY,
            price,
            price,
            price,
            price,
            Decimal(str(100 + lead_from)) if step >= lead_from else Decimal("1"),
            price,
            1,
            TimeFrame.D1,
        )
        for step in range(count)
    )


def _chain_fixture(months: int, bars_each: int = 400):  # type: ignore[no-untyped-def]
    chain = ContractChain("CL", tuple(_contract(index) for index in range(months)))
    observations = {
        futures_symbol(_contract(index)): _bars(futures_symbol(_contract(index)), bars_each, index)
        for index in range(months)
    }
    return chain, observations


def _option_chain(strikes: int):  # type: ignore[no-untyped-def]
    """Strikes spread across 100-200 whatever the count, so every one inverts.

    A chain widening with its size would put most contracts so far out of the
    money that they are refused at the no-arbitrage floor in a few microseconds,
    and the measurement would be of the refusal rather than of the inversion.
    """

    step = Decimal("100") / Decimal(strikes)
    contracts = tuple(
        OptionContract(
            "UND",
            Decimal("100") + step * i,
            YEAR,
            OptionType.CALL,
            ExerciseStyle.EUROPEAN,
            100,
        )
        for i in range(strikes)
    )
    prices = {
        occ_symbol(c): Decimal(str(black_scholes_value(c, 150.0, 0.25, 0.04, 1.0)))
        for c in contracts
    }
    return OptionChain("UND", 0.0, contracts), prices


_CONVENTION = MarketConvention(
    "XCME",
    "XCME",
    "USD",
    "USD",
    Decimal("1000"),
    TickSchedule.flat(Decimal("0.01")),
    LotSpecification.single_units(),
    SettlementRule(SettlementBasis.TRADE_DATE, 0),
)


def _holdings(count: int) -> list[ContractHolding]:
    return [
        ContractHolding(
            Position(
                f"A{i}", Decimal("10"), Decimal("75"), Decimal("75"), Decimal("0"), "USD", 0.0
            ),
            _CONVENTION,
        )
        for i in range(count)
    ]


_BOND = Bond(
    Decimal("100"),
    0.05,
    Compounding.SEMI_ANNUAL,
    date(2020, 1, 15),
    date(2030, 1, 15),
    DayCount.ACT_365_FIXED,
    "USD",
)
_SETTLEMENT = date(2025, 1, 15)
_OPTION = OptionContract("UND", Decimal("150"), YEAR, OptionType.CALL, ExerciseStyle.EUROPEAN, 100)
_PRICE = Decimal(str(black_scholes_value(_OPTION, 150.0, 0.25, 0.04, 1.0)))
_VENUE = VenueSpecification(
    "binance",
    8,
    FeeSchedule(Decimal("-1"), Decimal("5")),
    PriceSource.INDEX,
    Decimal("5"),
    "USDT",
)
_SPOT = FxRate("EUR", "USD", Decimal("1.10"), 0.0, "bench")
_TERMS = ForwardTerms(date(2026, 7, 2), date(2026, 1, 2), 0.05, 0.02, DayCount.ACT_360)
_TABLE = FxRates.of([_SPOT, FxRate("USD", "JPY", Decimal("150"), 0.0, "bench")])


def run_benchmark() -> None:
    print("Starting Multi-Asset Benchmark (v3.4)...\n")

    print("Per-call throughput:")
    _timed(
        "black_scholes_greeks",
        lambda: black_scholes_greeks(_OPTION, Decimal("150"), 0.25, 0.04, 0.0),
        50_000,
    )
    _timed(
        "implied_volatility",
        lambda: implied_volatility(_OPTION, _PRICE, Decimal("150"), 0.04, 0.0),
        5_000,
    )
    _timed("clean_price (20 flows)", lambda: clean_price(_BOND, 0.05, _SETTLEMENT), 20_000)
    _timed(
        "yield_from_clean_price",
        lambda: invert_yield(_BOND, Decimal("95.73489858"), _SETTLEMENT),
        500,
    )
    _timed("modified_duration", lambda: modified_duration(_BOND, 0.05, _SETTLEMENT), 20_000)
    _timed("convexity", lambda: convexity(_BOND, 0.05, _SETTLEMENT), 20_000)
    _timed(
        "cross_rate (EUR/JPY via USD)", lambda: _TABLE.cross_rate("EUR", "JPY", via="USD"), 100_000
    )
    _timed("covered_forward_rate", lambda: covered_forward_rate(_SPOT, _TERMS), 100_000)
    _timed(
        "trading_fee", lambda: trading_fee(_VENUE, Decimal("10000"), LiquidityRole.TAKER), 100_000
    )
    _timed(
        "cross_venue_dispersion (5)",
        lambda: cross_venue_dispersion({f"v{i}": Decimal(str(50000 + i)) for i in range(5)}),
        50_000,
    )

    curve = FuturesCurve(
        "CL",
        0.0,
        tuple(FuturesCurvePoint(float(i) * 30 * DAY, Decimal(str(70 + i))) for i in range(12)),
    )
    _timed("curve_shape (12 months)", lambda: curve_shape(curve), 50_000)

    funding_history = FundingRateHistory(
        "X", tuple(FundingRate("X", Decimal("0.0001"), i * 8 * 3600.0, 8) for i in range(90))
    )
    marks = {i * 8 * 3600.0: Decimal("60000") for i in range(90)}
    _timed(
        "accrued_funding (90 intervals)",
        lambda: accrued_funding(funding_history, Decimal("1"), marks, Decimal("1")),
        5_000,
    )

    print("\nScaling:")

    def roll_work(months: int):  # type: ignore[no-untyped-def]
        chain, _ = _chain_fixture(months)
        policy = RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 5)
        return lambda: roll_schedule(chain, policy)

    _scaling("roll_schedule, contracts", (100, 1_000, 10_000), roll_work)

    def segment_work(months: int):  # type: ignore[no-untyped-def]
        chain, observations = _chain_fixture(months, bars_each=months * 4)
        rolls = roll_schedule(
            chain, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations
        )
        return lambda: build_continuous_series(
            continuous_segments(chain, rolls, observations), AdjustmentMethod.BACK_ADJUSTED
        )

    # Both dimensions double together here -- twice the contracts, each with
    # twice the bars -- so 2x on this axis is 4x the observations. A ratio
    # near 4 is linear in the data; a ratio near 16 would be quadratic.
    _scaling("continuous series, contracts x bars", (20, 40, 80), segment_work)

    def surface_work(strikes: int):  # type: ignore[no-untyped-def]
        chain, prices = _option_chain(strikes)
        return lambda: surface_from_chain(chain, prices, Decimal("150"), 0.04, 0.0)

    _scaling("surface_from_chain, strikes", (50, 500, 5_000), surface_work)

    def exposure_work(count: int):  # type: ignore[no-untyped-def]
        holdings = _holdings(count)
        return lambda: contract_exposures(holdings)

    _scaling("contract_exposures, holdings", (1_000, 10_000, 100_000), exposure_work)

    def coverage_work(count: int):  # type: ignore[no-untyped-def]
        stamps = [float(i) * 60 for i in range(count) if i % 37]
        return lambda: coverage(stamps, 0.0, count * 60.0, 60.0)

    _scaling("crypto coverage, observations", (1_000, 10_000, 100_000), coverage_work)


if __name__ == "__main__":
    run_benchmark()
