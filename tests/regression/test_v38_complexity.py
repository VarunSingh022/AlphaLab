"""The v3.8 portfolio and risk paths stay near-linear, measured on every run.

The assertion is on the **growth ratio** between two input sizes, not on a
duration, so it is about the algorithm rather than the machine. Quadrupling the
input quadruples a linear path and multiplies a quadratic one by sixteen; the
bound sits between the two.

Every ratio is read with the stabilized method of
``test_lifecycle_registry_complexity.py`` -- process CPU time where the platform
keeps it finely, the collector disabled while timing, the two sizes interleaved
and the fastest of five samples compared -- imported from there rather than
copied, so there is one measurement method to maintain.

Which paths, and why these
---------------------------

Each is one whose obvious implementation is quadratic, and each was run against
that implementation -- the defect put back into a copy of the package -- and
failed:

* **Factor crowding over a large universe** -- one loading row per held asset,
  looked up by bisection. The v3.8 draft checked coverage per lookup by building
  a set of every asset the model carries, so a book over the whole universe paid
  for the universe once per holding.
* **A risk budget with a limit per strategy** -- the limits judged against
  buckets indexed once. Scanning a dimension's buckets per limit is
  ``limits x buckets``.
* **Valuing a multi-strategy book** -- instruments valued from an index of the
  lines, where filtering every line per instrument is ``instruments x lines``.
* **Allocating capital over many accounts** -- placements matched to accounts
  through an index, where looking an account up by scanning the plan is
  ``placements x accounts``.
* **Common exposures and pairwise overlap** -- one grouping pass and one set
  intersection per pair, never a membership scan of a list.

Measured on the release machine, the fixed paths read 4.0x to 4.2x for a 4x
input. With each defect put back, the same measurement read 20.4x (crowding),
11.3x (risk budget), 12.5x (valuation), 13.1x (allocation), 15.6x (common
exposures) and 13.1x (overlap) -- every one above the bound. The risk-budget and
allocation workloads were shaped for that: at first sizes their per-line and
per-conversion work hid the defect (5.6x and 7.4x), so the workload changed and
the bound did not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from alphalab.allocation.capital import (
    CapitalAccount,
    CapitalAllocationPlan,
    CapitalPlacement,
    FixedAmounts,
    OversubscriptionRule,
    allocate_capital,
)
from alphalab.analytics.cross_strategy import (
    CommonDimension,
    common_exposures,
    factor_crowding,
    strategy_overlap,
)
from alphalab.analytics.risk_budget import (
    BudgetBasis,
    BudgetLimit,
    RiskBudget,
    RiskDimension,
    evaluate_risk_budget,
)
from alphalab.analytics.risk_model import Classification, CovarianceMatrix, FactorLoadings
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.portfolio.multi_strategy import MultiStrategyBook, StrategySleeve, value_book
from alphalab.portfolio.position import Position
from tests.regression.test_lifecycle_registry_complexity import _CLOCK, _timings

#: A ratio above linear and well below quadratic.
LINEAR_BOUND = 8.0

STRATEGIES = ("CARRY", "MOMENTUM", "REVERSAL")
RATES = FxRates.of(
    [
        FxRate("EUR", "USD", Decimal("1.08"), 0.0, "bench"),
        FxRate("USD", "EUR", Decimal("0.93"), 0.0, "bench"),
    ]
)


@dataclass(frozen=True, slots=True)
class _Line:
    strategy_id: str
    asset_id: str
    currency: str
    market_value: Decimal
    reporting_value: Decimal


def _asset(index: int) -> str:
    return f"A{index:06d}"


def _value(index: int, salt: int) -> Decimal:
    """A deterministic signed amount, cents included."""

    return Decimal((index * 7919 + salt * 104_729) % 200_001 - 100_000) / 100 or Decimal("1")


def _lines(count: int) -> list[_Line]:
    """Three strategies, each holding every one of ``count`` instruments."""

    return [
        _Line(strategy, _asset(index), "USD", _value(index, salt), _value(index, salt))
        for salt, strategy in enumerate(STRATEGIES)
        for index in range(count)
    ]


def _crowding(count: int) -> float:
    assets = [_asset(index) for index in range(count)]
    loadings = FactorLoadings.of(
        {
            asset: {
                "momentum": ((index * 31) % 97) / 97 - 0.5,
                "size": ((index * 17) % 89) / 89 - 0.5,
                "value": ((index * 13) % 83) / 83 - 0.5,
            }
            for index, asset in enumerate(assets)
        },
        source="bench",
        lineage={"momentum": "bench", "size": "bench", "value": "bench"},
        as_of=None,
    )
    lines = _lines(count)
    start = _CLOCK()
    factor_crowding(lines, loadings, capital=Decimal(100_000_000))
    return _CLOCK() - start


_BUDGET_ASSETS = tuple(_asset(index) for index in range(8))
_BUDGET_COVARIANCE = CovarianceMatrix.from_rows(
    _BUDGET_ASSETS,
    [
        [0.0004 * (1 + row / 10) if row == column else 0.0001 for column in range(8)]
        for row in range(8)
    ],
    currency="USD",
    period="1D",
    source="bench",
    observations=None,
)


def _risk_budget(count: int) -> float:
    """``count`` strategies holding two of eight assets each, with a limit per strategy.

    Two lines per strategy rather than eight, so a scan of the buckets per
    limit is not hidden behind the per-line work at these sizes.
    """

    strategies = [f"S{index:05d}" for index in range(count)]
    lines = []
    for salt, strategy in enumerate(strategies):
        for offset in (0, 3):
            asset = _BUDGET_ASSETS[(salt + offset) % len(_BUDGET_ASSETS)]
            lines.append(
                _Line(strategy, asset, "USD", _value(salt + 1, offset), _value(salt + 1, offset))
            )
    budget = RiskBudget(
        "bench",
        tuple(
            BudgetLimit(RiskDimension.STRATEGY, strategy, BudgetBasis.RELATIVE, 0.5, None, None)
            for strategy in strategies
        ),
        0.0,
    )
    start = _CLOCK()
    evaluate_risk_budget(
        lines,
        capital=Decimal(1_000_000_000),
        reporting_currency="USD",
        covariance=_BUDGET_COVARIANCE,
        budget=budget,
        classifications=(),
    )
    return _CLOCK() - start


def _book(count: int) -> MultiStrategyBook:
    """Three sleeves over ``count`` instruments, half of them in euros, one mark each."""

    sleeves = []
    for salt, strategy in enumerate(STRATEGIES):
        positions = {}
        for index in range(count):
            asset = _asset(index)
            mark = Decimal(10 + index % 90)
            quantity = Decimal((index * (salt + 3)) % 41 - 20) or Decimal(1)
            positions[asset] = Position(
                asset, quantity, mark, mark, Decimal(0), "EUR" if index % 2 else "USD", 0.0
            )
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


def _valuation(count: int) -> float:
    book = _book(count)
    start = _CLOCK()
    value_book(book, reporting_currency="USD", rates=RATES, as_of=1.0)
    return _CLOCK() - start


def _allocation(count: int) -> float:
    accounts = tuple(
        CapitalAccount(
            f"ACC{index:05d}",
            f"BROKER{index % 4}",
            "EUR" if index % 2 else "USD",
            Decimal(1_000_000),
            Decimal(index % 7 * 1000),
        )
        for index in range(count)
    )
    placements = tuple(
        CapitalPlacement(f"S{index:05d}-{leg}", f"M{index % 3}", account.account_id)
        for index, account in enumerate(accounts)
        for leg in (0, 1)
    )
    start = _CLOCK()
    plan = CapitalAllocationPlan(
        "bench",
        "USD",
        1.0,
        accounts,
        placements,
        FixedAmounts({placement: Decimal(400_000) for placement in placements}),
        (),
        OversubscriptionRule.REFUSE,
        Decimal(1),
    )
    allocate_capital(plan, RATES)
    return _CLOCK() - start


def _common(count: int) -> float:
    lines = _lines(count)
    sectors = Classification(
        "sector", {_asset(index): f"SECTOR{index % 11}" for index in range(count)}, "bench", None
    )
    start = _CLOCK()
    common_exposures(lines, by=CommonDimension.CLASSIFICATION, classification=sectors)
    common_exposures(lines, by=CommonDimension.INSTRUMENT, classification=None)
    return _CLOCK() - start


def _overlap(count: int) -> float:
    lines = _lines(count)
    start = _CLOCK()
    strategy_overlap(lines)
    return _CLOCK() - start


def _assert_near_linear(what: str, small: float, large: float, sizes: tuple[int, int]) -> None:
    ratio = large / max(small, 1e-6)
    assert ratio < LINEAR_BOUND, (
        f"{what}: {sizes[1]:,} took {large:.4f}s against {small:.4f}s for {sizes[0]:,} "
        f"({ratio:.1f}x for a {sizes[1] // sizes[0]}x input) -- the path has gone quadratic."
    )
    assert math.isfinite(ratio)


def test_factor_crowding_over_a_large_universe_is_near_linear() -> None:
    small, large = _timings(_crowding, 2_000, 8_000)
    _assert_near_linear("factor crowding", small, large, (2_000, 8_000))


def test_a_risk_budget_with_a_limit_per_strategy_is_near_linear() -> None:
    small, large = _timings(_risk_budget, 2_000, 8_000)
    _assert_near_linear("risk budget", small, large, (2_000, 8_000))


def test_valuing_a_multi_strategy_book_is_near_linear() -> None:
    small, large = _timings(_valuation, 1_000, 4_000)
    _assert_near_linear("book valuation", small, large, (1_000, 4_000))


def test_allocating_capital_over_many_accounts_is_near_linear() -> None:
    small, large = _timings(_allocation, 500, 2_000)
    _assert_near_linear("capital allocation", small, large, (500, 2_000))


def test_common_exposures_are_near_linear() -> None:
    small, large = _timings(_common, 2_000, 8_000)
    _assert_near_linear("common exposures", small, large, (2_000, 8_000))


def test_pairwise_overlap_is_near_linear_in_the_instruments() -> None:
    small, large = _timings(_overlap, 2_000, 8_000)
    _assert_near_linear("strategy overlap", small, large, (2_000, 8_000))
