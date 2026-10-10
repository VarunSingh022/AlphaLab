"""High-performance benchmark suite for the v3.8 portfolio and risk paths.

Seven computational paths, each on a workload a multi-strategy desk produces:

* **Construction** -- every objective over one constraint set, and a
  factor-neutral, sector-capped rebalance with gross and turnover limits, at
  two universe sizes. The dual active-set solver's cost grows with the cube of
  the universe; the two sizes show how steeply.
* **Risk-budget aggregation** -- a book's risk decomposed along five
  dimensions and judged against a limit per strategy.
* **Multi-strategy aggregation** -- a book's holdings aggregated across sleeves
  with each strategy's contribution kept.
* **Multi-currency valuation** -- the same book valued in dollars, and in euros
  through a derived cross rate, every conversion recorded.
* **Cross-strategy correlation** -- strategies' return series correlated, with
  the covariance underneath.
* **Factor crowding** -- per-factor alignment and concentration across
  strategies, and pairwise overlap of holdings.
* **Capital allocation** -- a plan over many accounts in three currencies, with
  limits on every dimension.

Each path is measured at two sizes, so the printed ops/sec is readable *and*
the scaling is visible. ``tests/regression/test_v38_complexity.py`` asserts the
near-linear paths' growth ratios; this prints the absolute numbers. Every input
is built before its timer starts, from integer recurrences: no clock beyond the
timer and no random number.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from functools import partial

from alphalab.allocation import (
    CapitalAccount,
    CapitalAllocationPlan,
    CapitalDimension,
    CapitalLimit,
    CapitalPlacement,
    EqualWeights,
    OversubscriptionRule,
    allocate_capital,
)
from alphalab.analytics import (
    BudgetBasis,
    BudgetLimit,
    Classification,
    CovarianceMatrix,
    FactorLoadings,
    RiskBudget,
    RiskDimension,
    StrategyReturns,
    evaluate_risk_budget,
    factor_crowding,
    strategy_overlap,
    strategy_return_correlation,
)
from alphalab.portfolio import (
    FxRate,
    FxRates,
    MultiStrategyBook,
    Position,
    StrategySleeve,
    value_book,
)
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio_optimizer import (
    ConstraintSet,
    ConstructionObjective,
    ConstructionProblem,
    ExpectedReturns,
    ExposureRange,
    FactorBound,
    GroupBound,
    MaximumDiversification,
    MeanVariance,
    MinimumVariance,
    RiskParity,
    SolverSettings,
    TurnoverLimit,
    WeightBounds,
    construct,
)

CURRENCIES = ("EUR", "JPY", "USD")
RATES = FxRates.of(
    [
        FxRate("EUR", "USD", Decimal("1.0850"), 0.0, "benchmark desk"),
        FxRate("JPY", "USD", Decimal("0.006700"), 0.0, "benchmark desk"),
    ]
).with_inverses()


@dataclass(frozen=True, slots=True)
class _Line:
    strategy_id: str
    asset_id: str
    currency: str
    market_value: Decimal
    reporting_value: Decimal


def _timed(label: str, count: int, work: Callable[[], object]) -> object:
    start = time.perf_counter()
    result = work()
    duration = time.perf_counter() - start
    print(f"  {label:<64} {duration:.4f}s, {count / max(duration, 1e-9):>12,.0f} ops/sec")
    return result


def _asset(index: int) -> str:
    return f"A{index:05d}"


def _signed(index: int, salt: int, span: int) -> int:
    """A deterministic integer in ``[-span, span]``."""

    return (index * 7919 + salt * 104_729) % (2 * span + 1) - span


def _covariance(size: int) -> CovarianceMatrix:
    """``B B' + D``: three factors and a positive diagonal -- positive definite."""

    names = [_asset(index) for index in range(size)]
    loadings = [
        [(((index + 1) * (factor + 3) * 37) % 100) / 100.0 - 0.3 for factor in range(3)]
        for index in range(size)
    ]
    rows = [
        [
            1e-4 * sum(loadings[i][f] * loadings[j][f] for f in range(3))
            + (1e-4 * (1.0 + (i % 7) / 7.0) if i == j else 0.0)
            for j in range(size)
        ]
        for i in range(size)
    ]
    return CovarianceMatrix.from_rows(
        names, rows, currency="USD", period="1D", source="benchmark factor model", observations=None
    )


def _factor_model(names: list[str], factors: int) -> FactorLoadings:
    return FactorLoadings.of(
        {
            name: {f"f{factor}": _signed(index, factor, 100) / 100.0 for factor in range(factors)}
            for index, name in enumerate(names)
        },
        source="benchmark",
        lineage={f"f{factor}": "benchmark" for factor in range(factors)},
        as_of=None,
    )


# --------------------------------------------------------------------------- #
# 1. Construction
# --------------------------------------------------------------------------- #


def benchmark_construction() -> None:
    print("\n[1] Construction: every objective, one constraint set (solves per second)")
    settings = SolverSettings(1e-9, 1e-9, 10_000)
    for size in (25, 50):
        covariance = _covariance(size)
        names = list(covariance.assets)
        returns = ExpectedReturns(
            {name: 0.0002 + _signed(index, 5, 100) * 1e-6 for index, name in enumerate(names)},
            "USD",
            "1D",
            "benchmark",
        )
        capped = ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(0.10))
        objectives: tuple[tuple[str, ConstructionObjective], ...] = (
            ("minimum variance", MinimumVariance()),
            ("mean-variance", MeanVariance(returns, 5.0)),
            ("maximum diversification", MaximumDiversification()),
            ("risk parity", RiskParity.equal()),
        )
        for label, objective in objectives:
            problem = ConstructionProblem(covariance, objective, capped, settings)
            _timed(f"construct: {label} ({size} assets)", 1, partial(construct, problem))
        sectors = Classification(
            "sector", {name: f"S{index % 5}" for index, name in enumerate(names)}, "bench", None
        )
        held = dict.fromkeys(names, 1.0 / size)
        neutral = ConstraintSet(
            ExposureRange.exactly(1.0),
            WeightBounds.uniform(-0.05, 0.10),
            max_gross_exposure=1.4,
            groups=(GroupBound(sectors, "S0", None, 0.25),),
            factors=(FactorBound.neutral(_factor_model(names, 2), "f0"),),
            turnover=TurnoverLimit(held, 0.8),
        )
        problem = ConstructionProblem(covariance, MeanVariance(returns, 5.0), neutral, settings)
        _timed(
            f"construct: factor-neutral, capped, gross, turnover ({size} assets)",
            1,
            partial(construct, problem),
        )


# --------------------------------------------------------------------------- #
# 2. Risk budgets
# --------------------------------------------------------------------------- #


def benchmark_risk_budgets() -> None:
    print("\n[2] Risk-budget aggregation: five dimensions and a limit per strategy (lines/sec)")
    covariance = _covariance(50)
    names = list(covariance.assets)
    sectors = Classification(
        "sector", {name: f"S{index % 5}" for index, name in enumerate(names)}, "bench", None
    )
    countries = Classification(
        "country", {name: f"C{index % 3}" for index, name in enumerate(names)}, "bench", None
    )
    for strategies in (100, 400):
        lines = [
            _Line(
                f"S{strategy:04d}",
                names[(strategy * 7 + leg) % 50],
                CURRENCIES[leg % 3],
                Decimal(_signed(strategy, leg, 50_000) or 1),
                Decimal(_signed(strategy, leg, 50_000) or 1),
            )
            for strategy in range(strategies)
            for leg in range(20)
        ]
        budget = RiskBudget(
            "bench",
            tuple(
                BudgetLimit(
                    RiskDimension.STRATEGY, f"S{s:04d}", BudgetBasis.RELATIVE, 0.2, None, None
                )
                for s in range(strategies)
            ),
            0.0,
        )
        _timed(
            f"evaluate_risk_budget ({len(lines):,} lines, {strategies} limits)",
            len(lines),
            partial(
                evaluate_risk_budget,
                lines,
                capital=Decimal(100_000_000),
                reporting_currency="USD",
                covariance=covariance,
                budget=budget,
                classifications=(sectors, countries),
            ),
        )


# --------------------------------------------------------------------------- #
# 3 and 4. A multi-strategy book, aggregated and valued
# --------------------------------------------------------------------------- #


def _book(instruments: int) -> MultiStrategyBook:
    sleeves = []
    for salt, strategy in enumerate(("CARRY", "MOMENTUM", "VALUE")):
        positions = {}
        for index in range(instruments):
            asset = _asset(index)
            currency = CURRENCIES[index % 3]
            mark = Decimal(10 + index % 90) * (Decimal(100) if currency == "JPY" else Decimal(1))
            quantity = Decimal(_signed(index, salt, 40) or 1)
            positions[asset] = Position(asset, quantity, mark, mark, Decimal(0), currency, 0.0)
        sleeves.append(
            StrategySleeve(
                strategy,
                positions,
                CurrencyAmounts.single(Decimal(1_000_000), "USD"),
                CurrencyAmounts(),
                CurrencyAmounts(),
            )
        )
    return MultiStrategyBook("BENCH", tuple(sleeves), CurrencyAmounts())


def benchmark_multi_strategy() -> None:
    print("\n[3] Multi-strategy aggregation: holdings with provenance (positions/sec)")
    for instruments in (2_000, 8_000):
        book = _book(instruments)
        positions = 3 * instruments
        _timed(f"holdings(), 3 sleeves ({positions:,} positions)", positions, book.holdings)
        _timed(f"book_id ({positions:,} positions)", positions, partial(getattr, book, "book_id"))


def benchmark_multi_currency() -> None:
    print("\n[4] Multi-currency valuation: every conversion recorded (positions/sec)")
    cross = RATES.cross_rate("JPY", "EUR", via="USD")
    with_cross = RATES.with_rate(cross)
    for instruments in (2_000, 8_000):
        book = _book(instruments)
        positions = 3 * instruments
        _timed(
            f"value_book in USD ({positions:,} positions)",
            positions,
            partial(value_book, book, reporting_currency="USD", rates=RATES, as_of=1.0),
        )
        _timed(
            f"value_book in EUR, via a derived JPY/EUR ({positions:,} positions)",
            positions,
            partial(value_book, book, reporting_currency="EUR", rates=with_cross, as_of=1.0),
        )


# --------------------------------------------------------------------------- #
# 5. Correlation across strategies
# --------------------------------------------------------------------------- #


def benchmark_correlation() -> None:
    print("\n[5] Cross-strategy correlation: 250 daily returns each (strategies/sec)")
    for strategies in (10, 40):
        series = [
            StrategyReturns(
                f"S{index:03d}",
                tuple(_signed(day, index, 200) * 1e-4 for day in range(250)),
                "USD",
                "1D",
            )
            for index in range(strategies)
        ]
        _timed(
            f"strategy_return_correlation ({strategies} strategies)",
            strategies,
            partial(strategy_return_correlation, series, source="benchmark"),
        )


# --------------------------------------------------------------------------- #
# 6. Crowding and overlap
# --------------------------------------------------------------------------- #


def benchmark_crowding() -> None:
    print("\n[6] Factor crowding and overlap: 3 strategies, 5 factors (lines/sec)")
    for assets in (2_000, 8_000):
        names = [_asset(index) for index in range(assets)]
        loadings = _factor_model(names, 5)
        lines = [
            _Line(
                strategy,
                name,
                "USD",
                Decimal(_signed(i, salt, 900) or 1),
                Decimal(_signed(i, salt, 900) or 1),
            )
            for salt, strategy in enumerate(("CARRY", "MOMENTUM", "VALUE"))
            for i, name in enumerate(names)
        ]
        _timed(
            f"factor_crowding ({len(lines):,} lines)",
            len(lines),
            partial(factor_crowding, lines, loadings, capital=Decimal(100_000_000)),
        )
        _timed(
            f"strategy_overlap ({len(lines):,} lines)",
            len(lines),
            partial(strategy_overlap, lines),
        )


# --------------------------------------------------------------------------- #
# 7. Capital allocation
# --------------------------------------------------------------------------- #


def benchmark_capital() -> None:
    print("\n[7] Capital allocation: three currencies, limits on every dimension (placements/sec)")
    for count in (500, 2_000):
        accounts = tuple(
            CapitalAccount(
                f"ACC{index:05d}",
                f"BROKER{index % 4}",
                CURRENCIES[index % 3],
                Decimal(10_000_000 if index % 3 != 1 else 1_500_000_000),
                Decimal(0),
            )
            for index in range(count)
        )
        placements = tuple(
            CapitalPlacement(f"S{index:05d}-{leg}", f"M{index % 5}", account.account_id)
            for index, account in enumerate(accounts)
            for leg in (0, 1)
        )
        limits = (
            CapitalLimit(CapitalDimension.BROKER, "BROKER0", Decimal("0.5"), None),
            CapitalLimit(CapitalDimension.CURRENCY, "JPY", None, Decimal("0.01")),
            CapitalLimit(CapitalDimension.MARKET, "M0", Decimal("0.5"), None),
        )
        plan = CapitalAllocationPlan(
            "bench", "USD", 1.0, accounts, placements, EqualWeights(Decimal("0.8")), limits,
            OversubscriptionRule.PRO_RATA, Decimal("1000"),
        )  # fmt: skip
        _timed(
            f"allocate_capital ({len(placements):,} placements, {count:,} accounts)",
            len(placements),
            partial(allocate_capital, plan, RATES),
        )


def run_benchmark() -> None:
    print("=" * 94)
    print(
        "AlphaLab -- portfolio construction, risk budgets, multi-strategy books, capital"
        " (added in v3.8)"
    )
    print("=" * 94)
    benchmark_construction()
    benchmark_risk_budgets()
    benchmark_multi_strategy()
    benchmark_multi_currency()
    benchmark_correlation()
    benchmark_crowding()
    benchmark_capital()
    print()


if __name__ == "__main__":
    run_benchmark()
