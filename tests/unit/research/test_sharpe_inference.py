"""Probabilistic and deflated Sharpe ratios (ledger OFE-005).

The closed forms are checked against their definitions; the statistical claims
-- that PSR is a calibrated probability under its assumptions, and that DSR
removes the selection bias PSR suffers when applied to the best of many trials
-- are checked by simulation. Every simulation draws from a seeded generator,
so each is a deterministic test of a fixed sample rather than a flaky one.
"""

import math
import random

import pytest

from alphalab.common.statistics import normal_cdf, normal_quantile, standardized_moments
from alphalab.research import ResearchValidationError
from alphalab.research.sharpe_inference import (
    EULER_MASCHERONI,
    MINIMUM_OBSERVATIONS,
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    per_period_sharpe,
    probabilistic_sharpe_ratio,
)


def _normal_returns(seed: int, count: int, drift: float = 0.0) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(drift, 0.01) for _ in range(count)]


def test_psr_is_its_closed_form() -> None:
    returns = _normal_returns(1, 500, drift=0.0008)
    inference = probabilistic_sharpe_ratio(returns, 0.02)
    sharpe = per_period_sharpe(returns)
    moments = standardized_moments(returns)
    spread = 1.0 - moments.skewness * sharpe + (moments.kurtosis - 1.0) / 4.0 * sharpe**2
    expected = normal_cdf((sharpe - 0.02) * math.sqrt(499) / math.sqrt(spread))
    assert inference.probabilistic_sharpe == pytest.approx(expected, rel=1e-14)
    assert inference.sharpe == sharpe
    assert inference.standard_error == pytest.approx(math.sqrt(spread / 499), rel=1e-14)
    assert inference.p_value == pytest.approx(1.0 - expected, rel=1e-12)


def test_the_estimate_itself_is_the_even_money_benchmark() -> None:
    returns = _normal_returns(2, 250, drift=0.0005)
    inference = probabilistic_sharpe_ratio(returns, per_period_sharpe(returns))
    assert inference.probabilistic_sharpe == 0.5


def test_negative_skew_and_fat_tails_widen_the_standard_error() -> None:
    """A strategy that earns a premium and sometimes crashes is charged for the crashes."""

    rng = random.Random(3)
    crashy = [(0.004 if index % 20 else -0.05) + rng.gauss(0.0, 0.001) for index in range(400)]
    inference = probabilistic_sharpe_ratio(crashy, 0.0)
    assert inference.sharpe > 0.0
    assert inference.skewness < -2.0
    assert inference.kurtosis > 5.0
    normal_theory = math.sqrt((1.0 + inference.sharpe**2 / 2.0) / (inference.observations - 1))
    assert inference.standard_error > 1.1 * normal_theory


def test_psr_is_calibrated_under_the_null() -> None:
    """Worthless iid normal returns clear PSR > 0.95 about 5% of the time."""

    rng = random.Random(20260928)
    runs = 300
    cleared = sum(
        probabilistic_sharpe_ratio(
            [rng.gauss(0.0, 0.01) for _ in range(250)], 0.0
        ).probabilistic_sharpe
        > 0.95
        for _ in range(runs)
    )
    assert 0.01 <= cleared / runs <= 0.09


def test_psr_detects_real_skill() -> None:
    rng = random.Random(7)
    runs = 100
    cleared = sum(
        probabilistic_sharpe_ratio(
            [rng.gauss(0.002, 0.01) for _ in range(250)], 0.0
        ).probabilistic_sharpe
        > 0.95
        for _ in range(runs)
    )
    assert cleared / runs >= 0.8


def test_deflation_removes_the_selection_bias_of_the_best_trial() -> None:
    """The best of twenty worthless strategies usually passes PSR, and not DSR."""

    rng = random.Random(11)
    repetitions, trials = 100, 20
    psr_passes = dsr_passes = 0
    for _ in range(repetitions):
        series = [[rng.gauss(0.0, 0.01) for _ in range(250)] for _ in range(trials)]
        sharpes = [per_period_sharpe(returns) for returns in series]
        best = max(range(trials), key=lambda index: sharpes[index])
        psr_passes += probabilistic_sharpe_ratio(series[best], 0.0).probabilistic_sharpe > 0.95
        deflated = deflated_sharpe_ratio(series[best], sharpes, independent_trials=trials)
        dsr_passes += deflated.deflated_sharpe > 0.95
    assert psr_passes / repetitions > 0.4
    assert dsr_passes / repetitions <= 0.05


def test_the_expected_maximum_is_its_closed_form_and_grows_with_the_search() -> None:
    variance = 0.004
    expected = math.sqrt(variance) * (
        (1.0 - EULER_MASCHERONI) * normal_quantile(1.0 - 1.0 / 50)
        + EULER_MASCHERONI * normal_quantile(1.0 - 1.0 / (50 * math.e))
    )
    assert expected_maximum_sharpe(50, variance) == pytest.approx(expected, rel=1e-15)
    assert expected_maximum_sharpe(10, variance) < expected_maximum_sharpe(100, variance)
    assert expected_maximum_sharpe(10, 0.001) < expected_maximum_sharpe(10, 0.01)
    assert expected_maximum_sharpe(10, 0.0) == 0.0


def test_the_deflated_result_records_what_it_deflated_by() -> None:
    returns = _normal_returns(5, 300, drift=0.001)
    trials = [0.01, 0.05, -0.02, 0.08, 0.03]
    deflated = deflated_sharpe_ratio(returns, trials, independent_trials=4)
    assert deflated.independent_trials == 4
    assert deflated.trial_sharpe_variance == pytest.approx(
        sum((t - sum(trials) / 5) ** 2 for t in trials) / 4
    )
    assert deflated.expected_maximum == expected_maximum_sharpe(4, deflated.trial_sharpe_variance)
    assert deflated.inference.benchmark_sharpe == deflated.expected_maximum
    assert deflated.deflated_sharpe == deflated.inference.probabilistic_sharpe


@pytest.mark.parametrize(
    ("returns", "benchmark", "message"),
    [
        ([0.01] * (MINIMUM_OBSERVATIONS - 1), 0.0, "at least 30"),
        ([0.01] * MINIMUM_OBSERVATIONS, 0.0, "no dispersion"),
        ([0.01, -0.01] * 20, math.nan, "benchmark_sharpe"),
        ([0.01, math.inf] * 20, 0.0, "undefined"),
    ],
)
def test_psr_refusals(returns: list[float], benchmark: float, message: str) -> None:
    with pytest.raises(ResearchValidationError, match=message):
        probabilistic_sharpe_ratio(returns, benchmark)


@pytest.mark.parametrize(
    ("trials", "count", "message"),
    [
        ([0.1], 1, "at least 2"),
        ([0.1, 0.2], 3, "cannot exceed"),
        ([0.1, 0.2], 1, "one trial"),
        ([0.1, math.nan], 2, "trial_sharpes"),
    ],
)
def test_dsr_refusals(trials: list[float], count: int, message: str) -> None:
    with pytest.raises(ResearchValidationError, match=message):
        deflated_sharpe_ratio(_normal_returns(9, 60), trials, independent_trials=count)


def test_the_number_of_trials_is_required() -> None:
    with pytest.raises(TypeError, match="independent_trials"):
        deflated_sharpe_ratio(_normal_returns(9, 60), [0.1, 0.2])  # type: ignore[call-arg]


@pytest.mark.parametrize(("count", "variance"), [(True, 0.1), (2.0, 0.1), (5, -0.1), (5, math.inf)])
def test_expected_maximum_refusals(count: int, variance: float) -> None:
    with pytest.raises(ResearchValidationError):
        expected_maximum_sharpe(count, variance)
