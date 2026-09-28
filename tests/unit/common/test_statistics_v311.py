"""The v3.11 additions to the statistics authority: normal, moments, Newey-West."""

import math
import random

import pytest

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import (
    newey_west_standard_error,
    normal_cdf,
    normal_quantile,
    standardized_moments,
)


def test_the_normal_distribution_at_its_landmarks() -> None:
    assert normal_cdf(0.0) == 0.5
    assert normal_quantile(0.975) == pytest.approx(1.959963984540054, abs=1e-15)
    assert normal_cdf(1.959963984540054) == pytest.approx(0.975, abs=1e-15)
    for probability in (1e-12, 0.01, 0.3, 0.5, 0.77, 0.999):
        assert normal_cdf(normal_quantile(probability)) == pytest.approx(probability, rel=1e-12)


@pytest.mark.parametrize("probability", [0.0, 1.0, -0.1, 1.5, math.nan])
def test_the_quantile_is_refused_where_it_is_infinite(probability: float) -> None:
    with pytest.raises(AlphaLabValidationError, match="strictly inside"):
        normal_quantile(probability)


def test_the_cdf_of_nan_is_refused() -> None:
    with pytest.raises(AlphaLabValidationError, match="nan"):
        normal_cdf(math.nan)


def test_standardized_moments_by_hand() -> None:
    # deviations -3,-2,-1,0,6: sum of cubes 180, of fourth powers 1394, s^2 = 12.5
    moments = standardized_moments([1.0, 2.0, 3.0, 4.0, 10.0])
    assert moments.skewness == pytest.approx(180.0 / (5 * 12.5**1.5), rel=1e-14)
    assert moments.kurtosis == pytest.approx(1394.0 / (5 * 12.5**2), rel=1e-14)
    assert moments.observations == 5


def test_a_symmetric_sample_has_no_skew() -> None:
    moments = standardized_moments([-2.0, -1.0, 0.0, 1.0, 2.0])
    assert moments.skewness == 0.0


@pytest.mark.parametrize("values", [[1.0], [2.0, 2.0, 2.0], [1.0, math.nan, 2.0]])
def test_moments_are_refused_where_undefined(values: list[float]) -> None:
    with pytest.raises(AlphaLabValidationError):
        standardized_moments(values)


def test_newey_west_by_hand() -> None:
    # mean 2.5, deviations -1.5 -0.5 0.5 1.5: g0 = 1.25, g1 = 0.3125
    assert newey_west_standard_error([1.0, 2.0, 3.0, 4.0], 0) == pytest.approx(math.sqrt(1.25 / 4))
    # S = 1.25 + 2 * (1 - 1/2) * 0.3125 = 1.5625, SE = sqrt(1.5625 / 4) = 0.625
    assert newey_west_standard_error([1.0, 2.0, 3.0, 4.0], 1) == pytest.approx(0.625)


def test_overlap_widens_the_standard_error() -> None:
    """Sums over overlapping windows are autocorrelated; the naive error understates them."""

    rng = random.Random(4)
    noise = [rng.gauss(0, 1) for _ in range(600)]
    horizon = 5
    overlapping = [math.fsum(noise[i : i + horizon]) for i in range(len(noise) - horizon)]
    naive = newey_west_standard_error(overlapping, 0)
    corrected = newey_west_standard_error(overlapping, horizon - 1)
    assert corrected > 1.8 * naive


@pytest.mark.parametrize(
    ("values", "lag", "message"),
    [
        ([1.0, 2.0, 3.0], -1, "non-negative"),
        ([1.0, 2.0, 3.0], True, "non-negative"),
        ([1.0, 2.0], 1, "more than 2"),
        ([1.0, 1.0, 1.0, 1.0], 1, "no variation"),
        ([1.0, math.inf, 3.0, 4.0], 1, "inf"),
    ],
)
def test_newey_west_refusals(values: list[float], lag: int, message: str) -> None:
    with pytest.raises(AlphaLabValidationError, match=message):
        newey_west_standard_error(values, lag)
