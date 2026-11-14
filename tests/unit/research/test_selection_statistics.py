"""Selection statistics: the trial matrix, PBO, MinTRL, the adjusted PSR (v4.3, ADR-0054).

PBO is held to an exact-enumeration oracle written independently of the
implementation: every split enumerated, every mean and Sharpe ratio compared in
exact ``Fraction`` arithmetic over integer data, ranks counted from the
comparisons. MinTRL is held to its defining identity, that the probabilistic
Sharpe ratio over exactly that many observations is ``1 - alpha``. The
adjusted PSR is held to the independent PSR at no lags, value for value, and to
the direction serial correlation must move it.
"""

from __future__ import annotations

import itertools
import math
import random
from fractions import Fraction

import pytest

from alphalab.common.estimate import NumericalClass
from alphalab.common.statistics import normal_cdf
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.selection import (
    MAXIMUM_BLOCKS,
    SelectionMeasure,
    TrialMatrix,
    Trim,
    autocorrelation_adjusted_probabilistic_sharpe_ratio,
    minimum_track_record_length,
    probability_of_backtest_overfitting,
)
from alphalab.research.sharpe_inference import probabilistic_sharpe_ratio


def _matrix(columns: dict[str, list[float]]) -> TrialMatrix:
    periods = len(next(iter(columns.values())))
    return TrialMatrix.from_columns(columns, [float(i) for i in range(periods)], "return")


def _integer_matrix(trials: int, periods: int, seed: int) -> dict[str, list[float]]:
    rng = random.Random(seed)
    return {f"t{j}": [float(rng.randint(-9, 9)) for _ in range(periods)] for j in range(trials)}


# --------------------------------------------------------------------------- #
# The trial matrix
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("labels", "instants", "columns", "match"),
    [
        (("a",), (0.0, 1.0), ((1.0, 2.0),), "at least two"),
        (("a", "a"), (0.0, 1.0), ((1.0, 2.0), (1.0, 2.0)), "repeat"),
        (("a", " "), (0.0, 1.0), ((1.0, 2.0), (1.0, 2.0)), "label"),
        (("a", "b"), (1.0, 0.0), ((1.0, 2.0), (1.0, 2.0)), "ascending"),
        (("a", "b"), (0.0, 1.0), ((1.0, 2.0), (1.0,)), "common set of instants"),
        (("a", "b"), (0.0, 1.0), ((1.0, math.nan), (1.0, 2.0)), "holds nan"),
        (("a", "b"), (0.0, 1.0), ((1.0, 2.0),), "each trial needs one"),
    ],
)
def test_a_malformed_trial_matrix_is_refused(
    labels: tuple[str, ...],
    instants: tuple[float, ...],
    columns: tuple[tuple[float, ...], ...],
    match: str,
) -> None:
    with pytest.raises(ResearchValidationError, match=match):
        TrialMatrix(labels, instants, columns, "return")


def test_a_trial_matrix_keeps_its_columns_by_label() -> None:
    matrix = _matrix({"fast": [1.0, 2.0, 3.0], "slow": [0.5, 0.25, 0.0]})

    assert (matrix.periods, matrix.trials) == (3, 2)
    assert matrix.column("slow") == (0.5, 0.25, 0.0)
    with pytest.raises(ResearchValidationError, match="No trial"):
        matrix.column("medium")
    with pytest.raises(ResearchValidationError, match="names the measure"):
        TrialMatrix(("a", "b"), (0.0,), ((1.0,), (2.0,)), "")


# --------------------------------------------------------------------------- #
# PBO against an exact-enumeration oracle
# --------------------------------------------------------------------------- #


def _exact_performance(values: list[Fraction], measure: SelectionMeasure) -> tuple[int, Fraction]:
    """A key that orders performances exactly: the mean, or the Sharpe ratio's sign and square."""

    count = len(values)
    centre = sum(values, Fraction(0)) / count
    if measure is SelectionMeasure.MEAN:
        return (0, centre)
    variance = sum(((v - centre) ** 2 for v in values), Fraction(0)) / (count - 1)
    if variance == 0:
        return (0 if centre == 0 else (2 if centre > 0 else -2), Fraction(0))
    squared = centre * centre / variance
    return (1, squared) if centre > 0 else ((-1, -squared) if centre < 0 else (0, Fraction(0)))


def _oracle_pbo(
    columns: dict[str, list[float]], blocks: int, measure: SelectionMeasure
) -> Fraction:
    labels = list(columns)
    periods = len(columns[labels[0]])
    length = periods // blocks
    exact = {label: [Fraction(v) for v in columns[label]] for label in labels}
    overfit = 0
    total = 0
    for inside in itertools.combinations(range(blocks), blocks // 2):
        rows_in = [t for b in inside for t in range(b * length, (b + 1) * length)]
        rows_out = [t for t in range(blocks * length) if t // length not in inside]
        in_sample = [
            _exact_performance([exact[name][t] for t in rows_in], measure) for name in labels
        ]
        out_sample = [
            _exact_performance([exact[name][t] for t in rows_out], measure) for name in labels
        ]
        best = max(range(len(labels)), key=lambda j: in_sample[j])
        below = sum(1 for key in out_sample if key < out_sample[best])
        equal = sum(1 for key in out_sample if key == out_sample[best])
        if 2 * below + equal + 1 <= len(labels) + 1:
            overfit += 1
        total += 1
    return Fraction(overfit, total)


@pytest.mark.parametrize("measure", list(SelectionMeasure))
@pytest.mark.parametrize(
    ("trials", "periods", "blocks", "seed"),
    [(3, 8, 4, 1), (5, 24, 6, 2), (8, 40, 8, 3), (4, 30, 10, 4)],
)
def test_pbo_equals_the_exact_enumeration(
    measure: SelectionMeasure, trials: int, periods: int, blocks: int, seed: int
) -> None:
    columns = _integer_matrix(trials, periods, seed)

    result = probability_of_backtest_overfitting(
        _matrix(columns), blocks=blocks, measure=measure, trim=Trim.NONE
    )

    assert result.probability == _oracle_pbo(columns, blocks, measure)
    assert result.combinations == math.comb(blocks, blocks // 2)
    assert len(result.selected) == len(result.logits) == result.combinations


def test_a_trial_that_dominates_every_period_is_never_an_overfit() -> None:
    columns = _integer_matrix(6, 32, 9)
    columns["dominant"] = [max(row) + 1.0 for row in zip(*columns.values(), strict=True)]

    result = probability_of_backtest_overfitting(
        _matrix(columns), blocks=8, measure=SelectionMeasure.MEAN, trim=Trim.NONE
    )

    assert result.probability == 0
    assert set(result.selected) == {"dominant"}
    assert all(logit > 0 for logit in result.logits)


def test_identical_trials_sit_at_the_median_and_count_as_overfit() -> None:
    """Every rank ties, so the midrank is (N + 1) / 2, the logit 0, and lambda <= 0 counts."""

    column = [float(v) for v in (3, -1, 2, 0, 5, -4, 1, 2)]
    result = probability_of_backtest_overfitting(
        _matrix({"a": column, "b": list(column), "c": list(column)}),
        blocks=4,
        measure=SelectionMeasure.MEAN,
        trim=Trim.NONE,
    )

    assert result.probability == 1
    assert set(result.logits) == {0.0}
    assert set(result.selected) == {"a"}
    assert "including 0" in result.convention


def test_pbo_of_pure_noise_is_near_one_half() -> None:
    rng = random.Random(2026)
    columns = {f"n{j}": [rng.gauss(0.0, 1.0) for _ in range(320)] for j in range(30)}

    result = probability_of_backtest_overfitting(
        _matrix(columns), blocks=10, measure=SelectionMeasure.SHARPE_RATIO, trim=Trim.NONE
    )

    assert 0.3 < result.probability < 0.8


def test_the_logits_are_the_exact_logarithms_of_the_midranks() -> None:
    columns = _integer_matrix(7, 28, 5)
    result = probability_of_backtest_overfitting(
        _matrix(columns), blocks=4, measure=SelectionMeasure.MEAN, trim=Trim.NONE
    )
    # Each logit is ln(d / (2N + 2 - d)) for an integer doubled midrank d.
    for logit in result.logits:
        ratios = [d / (16 - d) for d in range(2, 15)]
        assert any(
            abs(logit - math.log(ratio)) <= 4 * math.ulp(abs(logit) or 1.0) for ratio in ratios
        )


@pytest.mark.parametrize(
    ("trim", "kept"), [(Trim.OLDEST, slice(2, None)), (Trim.NEWEST, slice(0, 24))]
)
def test_trimming_drops_the_periods_it_says_from_the_end_it_says(trim: Trim, kept: slice) -> None:
    columns = _integer_matrix(4, 26, 6)
    trimmed = {label: values[kept] for label, values in columns.items()}

    result = probability_of_backtest_overfitting(
        _matrix(columns), blocks=4, measure=SelectionMeasure.MEAN, trim=trim
    )

    assert (result.trimmed, result.trim) == (2, trim)
    assert result.probability == _oracle_pbo(trimmed, 4, SelectionMeasure.MEAN)


def test_periods_that_do_not_fill_the_blocks_are_never_dropped_silently() -> None:
    with pytest.raises(ResearchValidationError, match="not done silently"):
        probability_of_backtest_overfitting(
            _matrix(_integer_matrix(3, 25, 1)),
            blocks=4,
            measure=SelectionMeasure.MEAN,
            trim=Trim.NONE,
        )


@pytest.mark.parametrize("blocks", [0, 1, 3, 22, MAXIMUM_BLOCKS + 2, 4.0, True])
def test_a_block_count_out_of_range_is_refused(blocks: int) -> None:
    with pytest.raises(ResearchValidationError, match="blocks"):
        probability_of_backtest_overfitting(
            _matrix(_integer_matrix(3, 24, 1)),
            blocks=blocks,
            measure=SelectionMeasure.MEAN,
            trim=Trim.NONE,
        )


def test_a_sharpe_ratio_over_one_period_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="two periods"):
        probability_of_backtest_overfitting(
            _matrix(_integer_matrix(3, 2, 1)),
            blocks=2,
            measure=SelectionMeasure.SHARPE_RATIO,
            trim=Trim.NONE,
        )


#: Computed under CPython 3.12.4, 3.13.18 and 3.14.10 on macOS arm64, identical
#: in every bit; CI holds it on Linux x86-64 as well.
PINNED_PBO: tuple[str, str, list[str]] = (
    "619/924",
    "-0x1.3b664d56c9174p+7",
    ["c5", "c5", "c5", "c5", "c5"],
)


def _pinned_pbo() -> tuple[str, str, list[str]]:
    rng = random.Random(77)
    columns = {f"c{j}": [rng.gauss(0.0005, 0.01) for _ in range(120)] for j in range(9)}
    result = probability_of_backtest_overfitting(
        _matrix(columns), blocks=12, measure=SelectionMeasure.SHARPE_RATIO, trim=Trim.NONE
    )
    digest = math.fsum(result.logits)
    return (str(result.probability), digest.hex(), list(result.selected[:5]))


def test_the_pinned_pbo_is_the_same_on_every_host() -> None:
    assert _pinned_pbo() == PINNED_PBO


# --------------------------------------------------------------------------- #
# MinTRL
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("sharpe", "skewness", "kurtosis", "benchmark", "alpha"),
    [(0.1, 0.0, 3.0, 0.0, 0.05), (0.08, -0.7, 6.0, 0.02, 0.05), (0.3, 0.5, 2.5, 0.1, 0.01)],
)
def test_at_mintrl_observations_the_psr_is_one_minus_alpha(
    sharpe: float, skewness: float, kurtosis: float, benchmark: float, alpha: float
) -> None:
    result = minimum_track_record_length(sharpe, skewness, kurtosis, benchmark, alpha)
    spread = 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe
    standard_error = math.sqrt(spread / (result.observations - 1.0))

    assert normal_cdf((sharpe - benchmark) / standard_error) == pytest.approx(
        1.0 - alpha, abs=1e-12
    )
    assert result.numerical_class is NumericalClass.HOST_LIBM


def test_mintrl_for_normal_returns_is_the_textbook_formula() -> None:
    """Normal returns: 1 + (1 + SR^2 / 2) (z / SR)^2; z for 5% is 1.6448536269514722."""

    result = minimum_track_record_length(0.25, 0.0, 3.0, 0.0, 0.05)

    assert result.observations == pytest.approx(
        1.0 + (1.0 + 0.25 * 0.25 / 2.0) * (1.6448536269514722 / 0.25) ** 2, rel=1e-14
    )


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ((0.1, 0.0, 3.0, 0.1, 0.05), "not above the benchmark"),
        ((0.05, 0.0, 3.0, 0.1, 0.05), "not above the benchmark"),
        ((0.1, 0.0, 3.0, 0.0, 1.0), "alpha"),
        ((0.1, 0.0, 3.0, 0.0, 0.0), "alpha"),
        ((math.nan, 0.0, 3.0, 0.0, 0.05), "finite"),
        ((2.0, 3.0, 1.0, 0.0, 0.05), "no positive variance"),
    ],
)
def test_mintrl_refuses_what_has_no_answer(arguments: tuple[float, ...], match: str) -> None:
    with pytest.raises(ResearchValidationError, match=match):
        minimum_track_record_length(*arguments)


# --------------------------------------------------------------------------- #
# The autocorrelation-adjusted PSR
# --------------------------------------------------------------------------- #


def test_with_no_lags_the_adjusted_psr_is_the_independent_one_value_for_value() -> None:
    rng = random.Random(3)
    returns = [rng.gauss(0.0008, 0.01) for _ in range(400)]

    adjusted = autocorrelation_adjusted_probabilistic_sharpe_ratio(returns, 0.02, lags=0)

    assert adjusted.inference == probabilistic_sharpe_ratio(returns, 0.02)
    assert adjusted.adjusted_spread == adjusted.independent_spread


def _autoregressive(coefficient: float, seed: int, count: int = 1000) -> list[float]:
    rng = random.Random(seed)
    values = [0.0]
    for _ in range(count - 1):
        values.append(coefficient * values[-1] + rng.gauss(0.0004, 0.01))
    return values


def test_positive_autocorrelation_widens_the_standard_error() -> None:
    returns = _autoregressive(0.5, 11)

    independent = probabilistic_sharpe_ratio(returns, 0.0)
    adjusted = autocorrelation_adjusted_probabilistic_sharpe_ratio(returns, 0.0, lags=12)

    # For AR(1) the long-run variance of the mean is (1 + rho) / (1 - rho), three times the iid one.
    assert adjusted.inference.standard_error > 1.4 * independent.standard_error
    assert abs(adjusted.inference.probabilistic_sharpe - 0.5) < abs(
        independent.probabilistic_sharpe - 0.5
    )


def test_negative_autocorrelation_narrows_it() -> None:
    returns = _autoregressive(-0.5, 12)

    independent = probabilistic_sharpe_ratio(returns, 0.0)
    adjusted = autocorrelation_adjusted_probabilistic_sharpe_ratio(returns, 0.0, lags=12)

    assert adjusted.inference.standard_error < 0.8 * independent.standard_error


@pytest.mark.parametrize("lags", [-1, 999, 1.5, True])
def test_a_lag_count_out_of_range_is_refused(lags: int) -> None:
    rng = random.Random(1)
    with pytest.raises(ResearchValidationError, match="lags"):
        autocorrelation_adjusted_probabilistic_sharpe_ratio(
            [rng.gauss(0.0, 1.0) for _ in range(100)], 0.0, lags=lags
        )


def test_the_adjusted_psr_needs_as_many_returns_as_the_independent_one() -> None:
    with pytest.raises(ResearchValidationError, match="at least 30"):
        autocorrelation_adjusted_probabilistic_sharpe_ratio([0.01, -0.01] * 10, 0.0, lags=1)
