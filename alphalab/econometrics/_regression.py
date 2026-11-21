"""Ordinary least squares with classical standard errors, every step class 2 (ADR-0058).

The coefficients are M2's Householder QR (:func:`~alphalab.common.linalg.least_squares`).
The standard errors are ``sqrt(s² [(X'X)⁻¹]_ii)``, with ``X'X`` summed exactly by
``fsum`` and inverted through its Cholesky factor, one column at a time. Every
operation is correctly rounded and in a fixed order, so two hosts agree on every
bit.

The long-run variance is the Newey-West estimator with Bartlett weights, which
:func:`long_run_variance` computes by the same rules.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Sequence
from dataclasses import dataclass

from alphalab.common.linalg import IllConditionedError, cholesky, least_squares, singular_values
from alphalab.econometrics.exceptions import EconometricsInputError

__all__ = [
    "OrdinaryLeastSquares",
    "long_run_variance",
    "ordinary_least_squares",
    "residual_moments",
]


@dataclass(frozen=True, slots=True)
class OrdinaryLeastSquares:
    """A least-squares fit and its classical inference.

    Attributes:
        coefficients: One per column of the design, in column order.
        standard_errors: ``sqrt(s² [(X'X)⁻¹]_ii)``, in the same order.
        residuals: ``y - X b``, in row order.
        sum_of_squares: The residuals' exactly rounded sum of squares.
        variance: ``s²``, the sum of squares over the residual degrees of freedom.
        observations: Rows in the design.
        condition: The design's 2-norm condition number.
    """

    coefficients: tuple[float, ...]
    standard_errors: tuple[float, ...]
    residuals: tuple[float, ...]
    sum_of_squares: float
    variance: float
    observations: int
    condition: float


def ordinary_least_squares(
    design: Sequence[Sequence[float]], target: Sequence[float], maximum_condition: float
) -> OrdinaryLeastSquares:
    """The least-squares fit of ``target`` on ``design``, with classical standard errors.

    Raises:
        EconometricsInputError: If the design has no residual degree of freedom.
        IllConditionedError: If the design's condition number exceeds
            ``maximum_condition``, from the least-squares solve.
    """

    solution = least_squares(design, target, maximum_condition)
    rows = len(design)
    width = len(design[0])
    if rows <= width:
        raise EconometricsInputError(
            f"A fit of {width} coefficients on {rows} observations leaves no residual degree "
            "of freedom, so its standard errors are undefined."
        )
    residuals = solution.residuals
    sum_of_squares = math.fsum(value * value for value in residuals)
    variance = sum_of_squares / (rows - width)
    cross = [
        [math.fsum(row[i] * row[j] for row in design) for j in range(width)] for i in range(width)
    ]
    factor = cholesky(cross)
    errors = []
    for index in range(width):
        unit = [0.0] * width
        unit[index] = 1.0
        diagonal = factor.solve(unit)[index]
        errors.append(math.sqrt(variance * diagonal))
    return OrdinaryLeastSquares(
        coefficients=solution.coefficients,
        standard_errors=tuple(errors),
        residuals=residuals,
        sum_of_squares=sum_of_squares,
        variance=variance,
        observations=rows,
        condition=solution.condition,
    )


def long_run_variance(residuals: Sequence[float], lags: int) -> float:
    """Newey and West's long-run variance with Bartlett weights over ``lags`` lags.

    ``gamma0 + 2 Σ_{j=1..l} (1 - j / (l + 1)) gamma_j``, where ``gamma_j`` is the residuals'
    ``j``-th autocovariance with divisor ``T``: non-negative by construction.

    Raises:
        EconometricsInputError: If ``lags`` is negative or not below the
            number of residuals.
    """

    count = len(residuals)
    if isinstance(lags, bool) or not isinstance(lags, int) or not 0 <= lags < count:
        raise EconometricsInputError(
            f"A long-run variance over {count} residuals takes 0 to {count - 1} lags, got {lags!r}."
        )
    autocovariances = [
        math.fsum(residuals[t] * residuals[t - j] for t in range(j, count)) / count
        for j in range(lags + 1)
    ]
    weighted = math.fsum((1.0 - j / (lags + 1)) * autocovariances[j] for j in range(1, lags + 1))
    return autocovariances[0] + 2.0 * weighted


def residual_moments(
    regressors: Sequence[Sequence[float]],
    targets: Sequence[Sequence[float]],
    maximum_condition: float,
) -> tuple[tuple[float, ...], ...]:
    """The cross-products of ``targets``' residuals on ``regressors``, by columns.

    ``R'R`` for ``R = (I - X (X'X)⁻¹ X') Y``, with ``X`` the regressors' columns
    and ``Y`` the targets'. Computed as M2's least squares computes one
    regression (:func:`~alphalab.common.linalg.least_squares`), for every
    target at once: Householder reflections reduce ``X``, the textbook
    ``-sign(x0)`` choice with ``+`` for zero, every inner product a
    :func:`math.fsum`. The same reflections carry each target. Because ``Q`` is
    orthogonal, the residuals' cross-products are those of the reflected
    targets' rows beyond the first ``k``. No regressor leaves the targets'
    cross-products as they are.

    Raises:
        EconometricsInputError: If the columns differ in length, or the
            regressors leave no row beyond them.
        IllConditionedError: If the regressors' condition number exceeds
            ``maximum_condition``, measured as M2 measures it: from the
            triangular factor's singular values.
    """

    height = len(targets[0])
    for column in (*regressors, *targets):
        if len(column) != height:
            raise EconometricsInputError(
                f"Every column has {height} rows, and one has {len(column)}."
            )
    width = len(regressors)
    if height <= width:
        raise EconometricsInputError(
            f"{width} regressors over {height} rows leave no residual to measure."
        )
    columns = [[float(value) for value in column] for column in regressors]
    reflected = [[float(value) for value in column] for column in targets]
    for j in range(width):
        pivot = columns[j]
        norm = math.hypot(*pivot[j:])
        if norm == 0.0:
            continue
        alpha = -math.copysign(norm, pivot[j])
        reflector = pivot[j:]
        reflector[0] -= alpha
        scale = math.fsum(v * v for v in reflector)
        if scale == 0.0:
            continue
        for column in (*columns[j:], *reflected):
            below = column[j:]
            factor = 2.0 * math.fsum(map(operator.mul, reflector, below)) / scale
            column[j:] = [c - factor * v for c, v in zip(below, reflector, strict=True)]
    if width:
        triangle = [[columns[k][i] if k >= i else 0.0 for k in range(width)] for i in range(width)]
        values = singular_values(triangle)
        condition = math.inf if values[-1] == 0.0 else values[0] / values[-1]
        if not condition <= maximum_condition:
            detail = (
                "rank deficient" if math.isinf(condition) else f"conditioned at {condition:.6g}"
            )
            raise IllConditionedError(
                f"The {height}x{width} regressors are {detail}, beyond the stated maximum of "
                f"{maximum_condition:g}: the residuals would be decided by rounding rather "
                "than by the data.",
                condition,
                maximum_condition,
            )
    tails = [column[width:] for column in reflected]
    return tuple(
        tuple(math.fsum(map(operator.mul, tails[i], tails[k])) for k in range(len(tails)))
        for i in range(len(tails))
    )
