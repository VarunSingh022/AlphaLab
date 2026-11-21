"""Least squares with classical standard errors, and the long-run variance (v4.3, ADR-0074).

Both are class 2 (ADR-0058), and both are held to exact arithmetic: the normal
equations solved in ``Fraction``, and the Bartlett-weighted autocovariances
summed by hand.
"""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from alphalab.econometrics._regression import long_run_variance, ordinary_least_squares
from alphalab.econometrics.exceptions import EconometricsInputError

DESIGN = [[1.0, 2.0], [1.0, 3.0], [1.0, 5.0], [1.0, 7.0], [1.0, 11.0], [1.0, 13.0]]
TARGET = [3.0, 4.0, 7.0, 9.0, 14.0, 18.0]


def _exact() -> tuple[list[Fraction], list[Fraction]]:
    """The coefficients and their variances, by the normal equations in exact arithmetic."""

    zero = Fraction(0)
    x = [[Fraction(v) for v in row] for row in DESIGN]
    y = [Fraction(v) for v in TARGET]
    a = sum((r[0] * r[0] for r in x), zero)
    b = sum((r[0] * r[1] for r in x), zero)
    d = sum((r[1] * r[1] for r in x), zero)
    det = a * d - b * b
    inverse = [[d / det, -b / det], [-b / det, a / det]]
    xty = [sum((r[i] * v for r, v in zip(x, y, strict=True)), zero) for i in range(2)]
    coefficients = [sum((inverse[i][j] * xty[j] for j in range(2)), zero) for i in range(2)]
    residuals = [
        v - sum((c * r[i] for i, c in enumerate(coefficients)), zero)
        for r, v in zip(x, y, strict=True)
    ]
    s2 = sum((e * e for e in residuals), zero) / (len(x) - 2)
    return coefficients, [s2 * inverse[i][i] for i in range(2)]


def test_the_fit_is_the_normal_equations_answer_in_exact_arithmetic() -> None:
    fit = ordinary_least_squares(DESIGN, TARGET, 1e8)
    coefficients, variances = _exact()

    for got, want in zip(fit.coefficients, coefficients, strict=True):
        assert got == pytest.approx(float(want), rel=1e-13)
    for got, want in zip(fit.standard_errors, variances, strict=True):
        assert got == pytest.approx(math.sqrt(float(want)), rel=1e-13)
    assert fit.observations == 6
    assert fit.variance == pytest.approx(fit.sum_of_squares / 4, rel=1e-15)


def test_a_fit_with_no_residual_degree_of_freedom_is_refused() -> None:
    with pytest.raises(EconometricsInputError, match="no residual degree of freedom"):
        ordinary_least_squares(DESIGN[:2], TARGET[:2], 1e8)


def test_the_long_run_variance_is_the_bartlett_weighted_sum_by_hand() -> None:
    residuals = [0.5, -1.0, 0.25, 1.5, -0.75, 0.0, 1.25, -0.5]
    count = len(residuals)

    def gamma(j: int) -> Fraction:
        return (
            sum(
                (Fraction(residuals[t]) * Fraction(residuals[t - j]) for t in range(j, count)),
                Fraction(0),
            )
            / count
        )

    lags = 3
    exact = gamma(0) + 2 * sum(
        (Fraction(lags + 1 - j, lags + 1) * gamma(j) for j in range(1, lags + 1)), Fraction(0)
    )

    assert long_run_variance(residuals, lags) == pytest.approx(float(exact), rel=1e-15)
    assert long_run_variance(residuals, 0) == float(gamma(0))
    with pytest.raises(EconometricsInputError, match="takes 0 to 7 lags"):
        long_run_variance(residuals, 8)
