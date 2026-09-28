"""Benchmark-relative statistics (ledger FEA-002), checked on series built to have them."""

import math
import random
from decimal import Decimal

import pytest

from alphalab.analytics import (
    AnalyticsValidationError,
    BenchmarkBasis,
    benchmark_statistics,
)

DAILY = BenchmarkBasis(periods_per_year=252.0, risk_free_rate=0.03)


def _levels(returns: list[float], start: float = 100.0) -> dict[float, float]:
    levels = {0.0: start}
    for index, value in enumerate(returns, start=1):
        levels[float(index)] = levels[float(index - 1)] * (1.0 + value)
    return levels


def _benchmark_returns(seed: int, count: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(0.0004, 0.01) for _ in range(count)]


def test_a_levered_benchmark_has_beta_two_and_full_correlation() -> None:
    q = _benchmark_returns(1, 300)
    stats = benchmark_statistics(_levels([2.0 * x for x in q]), _levels(q), DAILY)
    assert stats.observations == 300
    assert stats.beta == pytest.approx(2.0, rel=1e-9)
    assert stats.correlation == pytest.approx(1.0, abs=1e-12)
    assert stats.r_squared == pytest.approx(1.0, abs=1e-12)
    assert stats.up_capture == pytest.approx(2.0, rel=1e-9)
    assert stats.down_capture == pytest.approx(2.0, rel=1e-9)
    mean_q = sum(q) / len(q)
    assert stats.active_return == pytest.approx(mean_q * 252.0, rel=1e-9)
    # Free leverage earns the riskless rate over what beta alone explains.
    assert stats.alpha == pytest.approx(0.03, rel=1e-7)


def test_tracking_the_benchmark_exactly_has_no_active_risk() -> None:
    q = _benchmark_returns(2, 60)
    stats = benchmark_statistics(_levels(q), _levels(q), DAILY)
    assert stats.tracking_error == 0.0
    assert stats.information_ratio is None
    assert stats.active_return == 0.0
    assert stats.beta == pytest.approx(1.0)
    assert stats.relative_return == 0.0


def test_the_information_ratio_is_active_return_over_tracking_error() -> None:
    rng = random.Random(3)
    q = _benchmark_returns(3, 250)
    p = [x + rng.gauss(0.0002, 0.002) for x in q]
    stats = benchmark_statistics(_levels(p), _levels(q), DAILY)
    active = [a - b for a, b in zip(p, q, strict=True)]
    mean_active = sum(active) / len(active)
    deviation = math.sqrt(sum((a - mean_active) ** 2 for a in active) / (len(active) - 1))
    assert stats.tracking_error == pytest.approx(deviation * math.sqrt(252.0), rel=1e-12)
    assert stats.information_ratio == pytest.approx(
        mean_active * 252.0 / (deviation * math.sqrt(252.0)), rel=1e-12
    )
    growth_p = _levels(p)[250.0] / 100.0
    growth_q = _levels(q)[250.0] / 100.0
    assert stats.relative_return == pytest.approx(growth_p / growth_q - 1.0, rel=1e-12)


def test_levels_are_aligned_on_shared_instants_and_the_rest_counted() -> None:
    portfolio = {0.0: 100.0, 1.0: 101.0, 2.0: 102.0, 3.0: 99.0, 4.0: 103.0}
    benchmark = {0.0: 50.0, 2.0: 51.0, 4.0: 52.0, 5.0: 53.0}
    stats = benchmark_statistics(portfolio, benchmark, DAILY)
    assert stats.observations == 2
    assert stats.portfolio_only_instants == 2
    assert stats.benchmark_only_instants == 1
    # The first shared return spans 0 -> 2 on both sides.
    expected_first_active = (102.0 / 100.0 - 1.0) - (51.0 / 50.0 - 1.0)
    expected_second_active = (103.0 / 102.0 - 1.0) - (52.0 / 51.0 - 1.0)
    assert stats.active_return == pytest.approx(
        (expected_first_active + expected_second_active) / 2 * 252.0
    )


def test_a_constant_benchmark_leaves_its_ratios_undefined() -> None:
    q = [0.0] * 20
    p = _benchmark_returns(4, 20)
    stats = benchmark_statistics(_levels(p), _levels(q), DAILY)
    assert stats.beta is None
    assert stats.alpha is None
    assert stats.correlation is None
    assert stats.r_squared is None
    assert stats.up_capture is None
    assert stats.down_capture is None
    assert stats.tracking_error is not None


def test_a_benchmark_that_never_fell_has_no_down_capture() -> None:
    q = [0.01, 0.02, 0.005, 0.01]
    stats = benchmark_statistics(_levels([0.02, 0.01, 0.0, 0.03]), _levels(q), DAILY)
    assert stats.down_capture is None
    assert stats.up_capture is not None


def test_one_period_measures_no_dispersion() -> None:
    stats = benchmark_statistics({0.0: 100.0, 1.0: 101.0}, {0.0: 10.0, 1.0: 10.2}, DAILY)
    assert stats.observations == 1
    assert stats.tracking_error is None
    assert stats.information_ratio is None
    assert stats.beta is None
    assert stats.active_return == pytest.approx((0.01 - 0.02) * 252.0)


def test_decimal_levels_are_accepted() -> None:
    portfolio = {0.0: Decimal("100.00"), 1.0: Decimal("101.50"), 2.0: Decimal("100.75")}
    benchmark = {0.0: Decimal("10"), 1.0: Decimal("10.1"), 2.0: Decimal("10.05")}
    stats = benchmark_statistics(portfolio, benchmark, DAILY)
    assert stats.observations == 2


@pytest.mark.parametrize(
    ("portfolio", "benchmark", "message"),
    [
        ({0.0: 100.0, 1.0: 0.0}, {0.0: 1.0, 1.0: 1.0}, "positive, finite"),
        ({0.0: 100.0, 1.0: math.nan}, {0.0: 1.0, 1.0: 1.0}, "positive, finite"),
        ({0.0: 100.0, 1.0: True}, {0.0: 1.0, 1.0: 1.0}, "portfolio level"),
        ({0.0: 100.0, 1.0: 101.0}, {1.0: 1.0, 2.0: 1.0}, "share 1 instant"),
    ],
)
def test_malformed_levels_are_refused(
    portfolio: dict[float, float], benchmark: dict[float, float], message: str
) -> None:
    with pytest.raises(AnalyticsValidationError, match=message):
        benchmark_statistics(portfolio, benchmark, DAILY)


@pytest.mark.parametrize(
    ("periods", "rate"), [(0.0, 0.03), (-252.0, 0.03), (252.0, math.inf), (True, 0.03)]
)
def test_a_malformed_basis_is_refused(periods: float, rate: float) -> None:
    with pytest.raises(AnalyticsValidationError):
        BenchmarkBasis(periods_per_year=periods, risk_free_rate=rate)
