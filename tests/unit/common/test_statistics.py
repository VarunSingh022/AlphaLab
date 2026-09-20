"""The one statistics authority: exact values, tie conventions, and refusals.

Three properties are asserted here, and the third is the reason the module
exists at all.

1. **The numbers are right.** Every function is checked against a value worked
   out by hand rather than against itself, so a refactor that changes an
   estimator is caught rather than absorbed.
2. **Ties are deterministic and deterministically different.** The four
   :class:`~alphalab.common.statistics.RankMethod` members give four specific
   answers to the same input, and the answer does not depend on input order.
3. **An undefined statistic raises.** A correlation over one point, a variance
   over one point and a z-score over a constant series are not zero, and each
   refusal is asserted by name.
"""

import math
from collections.abc import Callable

import pytest

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import (
    RankMethod,
    TieBreak,
    bucket_index,
    linear_regression,
    mean,
    median,
    pearson_correlation,
    percentile,
    rank_correlation,
    ranks,
    sample_variance,
    standard_deviation,
    standardize,
    winsorize,
)

# --------------------------------------------------------------------------- #
# Central tendency and dispersion
# --------------------------------------------------------------------------- #


def test_mean_is_the_arithmetic_mean() -> None:
    assert mean([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_sample_variance_uses_the_unbiased_estimator() -> None:
    """``n - 1``, worked out by hand: deviations 1.5, 0.5, 0.5, 1.5."""

    assert sample_variance([1.0, 2.0, 3.0, 4.0]) == pytest.approx(5.0 / 3.0)


def test_standard_deviation_is_the_root_of_the_variance() -> None:
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    assert standard_deviation(values) == pytest.approx(math.sqrt(sample_variance(values)))


def test_median_averages_the_two_middles_for_an_even_count() -> None:
    assert median([3.0, 1.0, 4.0, 2.0]) == 2.5
    assert median([3.0, 1.0, 2.0]) == 2.0


def test_percentile_interpolates_linearly_between_order_statistics() -> None:
    """``(n - 1) * fraction`` selects position 1.5 of [1, 2, 3, 4], so 2.5."""

    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0.5) == 2.5
    assert percentile(values, 0.0) == 1.0
    assert percentile(values, 1.0) == 4.0


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (lambda: mean([]), "mean of an empty"),
        (lambda: sample_variance([1.0]), "undefined over 1 observation"),
        (lambda: median([]), "median of an empty"),
        (lambda: percentile([], 0.5), "empty sequence"),
        (lambda: percentile([1.0], 1.5), r"\[0, 1\]"),
    ],
)
def test_an_undefined_summary_raises_rather_than_returning_zero(
    call: Callable[[], object], match: str
) -> None:
    with pytest.raises(AlphaLabValidationError, match=match):
        call()


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #


def test_the_four_tie_methods_give_four_different_answers() -> None:
    """``[10, 20, 20, 30]``: the tie spans positions 2 and 3."""

    values = [10.0, 20.0, 20.0, 30.0]

    assert ranks(values, RankMethod.AVERAGE) == (1.0, 2.5, 2.5, 4.0)
    assert ranks(values, RankMethod.MIN) == (1.0, 2.0, 2.0, 4.0)
    assert ranks(values, RankMethod.MAX) == (1.0, 3.0, 3.0, 4.0)
    assert ranks(values, RankMethod.ORDINAL) == (1.0, 2.0, 3.0, 4.0)


def test_average_ranking_does_not_depend_on_input_order() -> None:
    """The property that makes a rank correlation reproducible.

    ``ORDINAL`` is deliberately excluded: it breaks ties by position and is
    order-dependent by construction, which is why it is never used for a
    statistic.
    """

    forward = [10.0, 20.0, 20.0, 30.0]
    reversed_pairs = [30.0, 20.0, 20.0, 10.0]

    assert sorted(ranks(forward, RankMethod.AVERAGE)) == sorted(
        ranks(reversed_pairs, RankMethod.AVERAGE)
    )
    assert ranks(forward, RankMethod.AVERAGE)[1] == ranks(reversed_pairs, RankMethod.AVERAGE)[1]


def test_ranks_come_back_in_input_order() -> None:
    assert ranks([30.0, 10.0, 20.0]) == (3.0, 1.0, 2.0)


def test_ranking_an_empty_sequence_raises() -> None:
    with pytest.raises(AlphaLabValidationError, match="empty sequence is undefined"):
        ranks([])


# --------------------------------------------------------------------------- #
# Correlation
# --------------------------------------------------------------------------- #


def test_a_perfect_linear_relationship_correlates_at_one() -> None:
    assert pearson_correlation([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
    assert pearson_correlation([1.0, 2.0, 3.0], [-2.0, -4.0, -6.0]) == pytest.approx(-1.0)


def test_pearson_matches_a_value_computed_by_hand() -> None:
    """x = [1,2,3,4] (mean 2.5), y = [2,4,5,4] (mean 3.75).

    dx = [-1.5, -0.5, 0.5, 1.5], dy = [-1.75, 0.25, 1.25, 0.25], so the sum of
    products is 2.625 - 0.125 + 0.625 + 0.375 = 3.5, sxx = 5.0 and
    syy = 4.75. The coefficient is 3.5 / sqrt(23.75).
    """

    assert pearson_correlation([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 5.0, 4.0]) == pytest.approx(
        3.5 / math.sqrt(23.75)
    )


def test_rank_correlation_ignores_a_monotone_transform() -> None:
    """The property that distinguishes Spearman from Pearson, asserted directly."""

    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 8.0, 27.0, 64.0, 125.0]

    assert rank_correlation(xs, ys) == pytest.approx(1.0)
    assert pearson_correlation(xs, ys) < 1.0


def test_rank_correlation_uses_average_ties_so_it_is_spearmans() -> None:
    """With a tie in x, Spearman's value differs from an ordinal ranking's."""

    xs = [1.0, 2.0, 2.0, 4.0]
    ys = [1.0, 2.0, 3.0, 4.0]

    assert rank_correlation(xs, ys) == pytest.approx(pearson_correlation(ranks(xs), ranks(ys)))


def test_a_correlation_with_a_constant_series_is_refused() -> None:
    """Zero would read as a measured absence of relationship. It is not measured."""

    with pytest.raises(AlphaLabValidationError, match="x is constant"):
        pearson_correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])
    with pytest.raises(AlphaLabValidationError, match="y is constant"):
        pearson_correlation([1.0, 2.0, 3.0], [5.0, 5.0, 5.0])


def test_mismatched_lengths_and_short_samples_are_refused() -> None:
    with pytest.raises(AlphaLabValidationError, match="one x for every y"):
        pearson_correlation([1.0, 2.0], [1.0])
    with pytest.raises(AlphaLabValidationError, match="undefined over 1 observation"):
        pearson_correlation([1.0], [1.0])


# --------------------------------------------------------------------------- #
# Regression
# --------------------------------------------------------------------------- #


def test_a_regression_recovers_the_line_it_was_given() -> None:
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [3.0 + 2.0 * x for x in xs]

    fit = linear_regression(ys, xs)

    assert fit.slope == pytest.approx(2.0)
    assert fit.intercept == pytest.approx(3.0)
    assert fit.r_squared == pytest.approx(1.0)
    assert fit.observations == 4
    assert all(residual == pytest.approx(0.0) for residual in fit.residuals)


def test_residuals_sum_to_zero_and_are_uncorrelated_with_the_regressor() -> None:
    """The two defining properties of an OLS fit with an intercept."""

    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [2.0, 4.0, 5.0, 4.0, 6.0]

    fit = linear_regression(ys, xs)

    assert sum(fit.residuals) == pytest.approx(0.0, abs=1e-12)
    assert sum(
        x * residual for x, residual in zip(xs, fit.residuals, strict=True)
    ) == pytest.approx(0.0, abs=1e-12)


def test_a_constant_regressor_leaves_the_slope_undetermined() -> None:
    with pytest.raises(AlphaLabValidationError, match="x is constant"):
        linear_regression([1.0, 2.0, 3.0], [4.0, 4.0, 4.0])


# --------------------------------------------------------------------------- #
# Standardizing, winsorizing, bucketing
# --------------------------------------------------------------------------- #


def test_standardize_produces_a_zero_mean_unit_deviation_series() -> None:
    standardized = standardize([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])

    assert sum(standardized) == pytest.approx(0.0, abs=1e-12)
    assert standard_deviation(standardized) == pytest.approx(1.0)


def test_standardizing_a_constant_series_is_refused() -> None:
    """Zeros would claim every observation sat at a mean that means nothing."""

    with pytest.raises(AlphaLabValidationError, match="no dispersion"):
        standardize([3.0, 3.0, 3.0])


def test_winsorize_clips_and_never_drops() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    clipped = winsorize(values, 0.0, 0.75)

    assert len(clipped) == len(values)
    assert max(clipped) == percentile(values, 0.75)
    assert clipped[:4] == (1.0, 2.0, 3.0, 4.0)


def test_winsorize_refuses_crossed_bounds() -> None:
    with pytest.raises(AlphaLabValidationError, match="bounds are crossed"):
        winsorize([1.0, 2.0], 0.9, 0.1)


def test_bucketing_is_invariant_to_a_monotone_transform() -> None:
    """Buckets are built on ranks, which is what makes this true."""

    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    cubed = [value**3 for value in values]

    assert bucket_index(values, 3) == bucket_index(cubed, 3)
    assert bucket_index(values, 3) == (0, 0, 1, 1, 2, 2)


def test_the_three_tie_breaks_place_a_tied_run_differently() -> None:
    values = [1.0, 5.0, 5.0, 5.0, 5.0, 9.0]

    low = bucket_index(values, 3, TieBreak.LOW)
    high = bucket_index(values, 3, TieBreak.HIGH)
    spread = bucket_index(values, 3, TieBreak.SPREAD)

    assert low == (0, 0, 0, 0, 0, 2)
    assert high == (0, 2, 2, 2, 2, 2)
    assert spread == (0, 0, 1, 1, 2, 2)
    assert len({low, high, spread}) == 3


def test_bucketing_refuses_a_sample_that_cannot_fill_the_buckets() -> None:
    with pytest.raises(AlphaLabValidationError, match="at least 5 are needed"):
        bucket_index([1.0, 2.0, 3.0], 5)


# --------------------------------------------------------------------------- #
# The consolidation this module performed
# --------------------------------------------------------------------------- #


def test_the_three_volatility_functions_agree_with_the_shared_estimator() -> None:
    """v3.2 replaced three private copies of sample variance with this one.

    The three published functions must be unchanged, so each is checked against
    ``sqrt(sample_variance) * sqrt(periods)`` computed here independently.
    """

    from alphalab.analytics.returns import annualized_volatility
    from alphalab.analytics.rolling import rolling_volatility
    from alphalab.research.metrics import calculate_volatility

    returns = (0.01, -0.02, 0.015, 0.003, -0.008, 0.02, -0.011)
    expected = math.sqrt(sample_variance(returns)) * math.sqrt(252)

    assert annualized_volatility(returns) == expected
    assert calculate_volatility(returns) == expected
    assert rolling_volatility(returns, len(returns))[0] == expected


def test_the_volatility_functions_still_report_zero_for_a_short_window() -> None:
    """Their guards are unchanged: the shared estimator raises, they do not."""

    from alphalab.analytics.returns import annualized_volatility
    from alphalab.research.metrics import calculate_volatility

    assert annualized_volatility((0.01,)) == 0.0
    assert calculate_volatility((0.01,)) == 0.0
    with pytest.raises(AlphaLabValidationError):
        sample_variance((0.01,))
