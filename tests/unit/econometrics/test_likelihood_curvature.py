"""The curvature routine every likelihood fit's standard errors share (ADR-0074).

Central differences are exact for a quadratic, to rounding: the curvature of
``f(x) = (x - c)' A (x - c) / 2`` is ``A`` and the covariance ``A⁻¹``, off the
diagonal too. A curvature that is not positive definite is refused.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from alphalab.econometrics._likelihood import curvature_covariance, curvature_standard_errors
from alphalab.econometrics.exceptions import EconometricsInputError

MATRIX = [[2.0, 0.5], [0.5, 1.0]]
CENTRE = [0.3, -0.2]


def _quadratic(point: Sequence[float]) -> float:
    d = [point[0] - CENTRE[0], point[1] - CENTRE[1]]
    return 0.5 * (
        MATRIX[0][0] * d[0] * d[0] + 2.0 * MATRIX[0][1] * d[0] * d[1] + MATRIX[1][1] * d[1] * d[1]
    )


def test_a_quadratics_covariance_is_its_matrix_inverted() -> None:
    determinant = MATRIX[0][0] * MATRIX[1][1] - MATRIX[0][1] * MATRIX[1][0]
    inverse = [
        [MATRIX[1][1] / determinant, -MATRIX[0][1] / determinant],
        [-MATRIX[1][0] / determinant, MATRIX[0][0] / determinant],
    ]
    covariance = curvature_covariance(_quadratic, CENTRE, 1e-4)

    for i in range(2):
        assert covariance[i] == pytest.approx(inverse[i], rel=1e-6)
    assert curvature_standard_errors(_quadratic, CENTRE, 1e-4) == pytest.approx(
        [math.sqrt(inverse[0][0]), math.sqrt(inverse[1][1])], rel=1e-6
    )


def test_a_curvature_that_is_not_positive_definite_is_refused() -> None:
    with pytest.raises(EconometricsInputError, match="not positive definite"):
        curvature_covariance(lambda point: -_quadratic(point), CENTRE, 1e-4)
