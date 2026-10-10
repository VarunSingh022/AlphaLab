"""Least squares by Householder QR, and the conditioning it refuses (v3.11, OFE-004)."""

import math
import random

import pytest

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.linalg import (
    IllConditionedError,
    condition_number,
    least_squares,
    singular_values,
)
from alphalab.common.statistics import linear_regression


def _design(rows: int, seed: int) -> list[list[float]]:
    rng = random.Random(seed)
    return [[1.0, rng.uniform(-1, 1), rng.uniform(-2, 2), rng.gauss(0, 1)] for _ in range(rows)]


def test_a_noiseless_fit_recovers_its_coefficients() -> None:
    design = _design(40, 7)
    truth = (0.5, 2.0, -3.0, 0.25)
    target = [math.fsum(x * b for x, b in zip(row, truth, strict=True)) for row in design]
    solution = least_squares(design, target, 1e6)
    assert solution.coefficients == pytest.approx(truth, abs=1e-12)
    assert max(abs(r) for r in solution.residuals) < 1e-12
    assert solution.observations == 40


def test_residuals_are_orthogonal_to_every_column() -> None:
    rng = random.Random(11)
    design = _design(60, 3)
    target = [rng.gauss(0, 1) for _ in design]
    solution = least_squares(design, target, 1e6)
    for column in range(4):
        dot = math.fsum(row[column] * r for row, r in zip(design, solution.residuals, strict=True))
        assert abs(dot) < 1e-12


def test_one_regressor_agrees_with_the_closed_form() -> None:
    rng = random.Random(5)
    xs = [rng.uniform(0, 10) for _ in range(30)]
    ys = [3.0 - 0.7 * x + rng.gauss(0, 0.5) for x in xs]
    closed = linear_regression(ys, xs)
    solution = least_squares([[1.0, x] for x in xs], ys, 1e6)
    assert solution.coefficients == pytest.approx((closed.intercept, closed.slope), rel=1e-12)
    assert solution.residuals == pytest.approx(closed.residuals, abs=1e-12)


def test_the_hilbert_matrix_condition_matches_its_published_value() -> None:
    hilbert = [[1.0 / (i + j + 1) for j in range(6)] for i in range(6)]
    assert condition_number(hilbert) == pytest.approx(1.495105864e7, rel=1e-8)


def test_singular_values_of_simple_matrices() -> None:
    assert singular_values([[3.0, 0.0], [0.0, 4.0]]) == (4.0, 3.0)
    first, second = singular_values([[1.0, 1.0], [1.0, 1.0]])
    assert first == pytest.approx(2.0)
    assert second == 0.0
    assert condition_number([[1.0, 1.0], [1.0, 1.0]]) == math.inf
    wide = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    tall = [[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]]
    assert singular_values(wide) == pytest.approx(singular_values(tall), rel=1e-14)


def test_a_rank_deficient_design_is_refused() -> None:
    # Dependent in exact arithmetic; rounding leaves a last pivot near 1e-16,
    # so the measured condition is astronomically large rather than infinite.
    design = [[1.0, x, 2.0 * x] for x in (1.0, 2.0, 3.0, 4.0, 5.0)]
    with pytest.raises(IllConditionedError, match="linearly dependent") as refused:
        least_squares(design, [1.0, 2.0, 3.0, 4.0, 6.0], 1e6)
    assert refused.value.condition > 1e15
    assert refused.value.maximum == 1e6
    # A column that is exactly zero leaves an exactly zero pivot: infinite.
    with pytest.raises(IllConditionedError, match="rank deficient") as exact:
        least_squares([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]], [1.0, 2.0, 3.0], 1e6)
    assert exact.value.condition == math.inf


def test_a_nearly_collinear_design_is_refused_above_the_bound_and_solved_below_it() -> None:
    rng = random.Random(2)
    xs = [rng.uniform(-1, 1) for _ in range(50)]
    design = [[1.0, x, x + 1e-7 * rng.gauss(0, 1)] for x in xs]
    target = [rng.gauss(0, 1) for _ in xs]
    with pytest.raises(IllConditionedError) as refused:
        least_squares(design, target, 1e6)
    assert 1e6 < refused.value.condition < math.inf
    solved = least_squares(design, target, 1e12)
    assert solved.condition == pytest.approx(refused.value.condition)


def test_the_same_inputs_give_the_same_floats() -> None:
    design = _design(25, 9)
    target = [row[1] * 3.0 + row[3] for row in design]
    assert least_squares(design, target, 1e6) == least_squares(design, target, 1e6)


@pytest.mark.parametrize(
    ("design", "target", "maximum", "message"),
    [
        ([], [], 1e6, "at least one row"),
        ([[]], [1.0], 1e6, "at least one column"),
        ([[1.0, 2.0], [1.0]], [1.0, 2.0], 1e6, "ragged"),
        ([[1.0, math.nan], [1.0, 2.0]], [1.0, 2.0], 1e6, "nan"),
        ([[1.0, 2.0], [1.0, 3.0]], [1.0], 1e6, "target"),
        ([[1.0, 2.0], [1.0, 3.0]], [1.0, math.inf], 1e6, "inf"),
        ([[1.0, 2.0, 3.0]], [1.0], 1e6, "underdetermined"),
        ([[1.0], [2.0]], [1.0, 2.0], 0.5, "maximum_condition"),
        ([[1.0], [2.0]], [1.0, 2.0], math.inf, "maximum_condition"),
    ],
)
def test_malformed_systems_are_refused(
    design: list[list[float]], target: list[float], maximum: float, message: str
) -> None:
    with pytest.raises(AlphaLabValidationError, match=message):
        least_squares(design, target, maximum)
