"""Standard errors from a likelihood's curvature, by central differences (ADR-0074).

The curvature of the negative log-likelihood at its minimum is measured by
central differences, each coordinate moved by ``step * max(1, |x_i|)``: second
differences on the diagonal, the four-point formula off it. It is factored by
M2's Cholesky and never inverted: column ``i`` of ``H⁻¹`` is the solve against
the ``i``-th unit vector, and standard error ``i`` is ``sqrt([H⁻¹]_ii)``. A
curvature that is not positive definite is refused, as no maximum to report
standard errors for.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from alphalab.common.linalg import NotPositiveDefiniteError, cholesky
from alphalab.econometrics.exceptions import EconometricsInputError

__all__ = ["curvature_covariance", "curvature_standard_errors"]


def curvature_covariance(
    negative_log_likelihood: Callable[[Sequence[float]], float],
    point: Sequence[float],
    step: float,
) -> list[list[float]]:
    """``H⁻¹``, ``H`` the central-difference curvature of ``negative_log_likelihood``.

    Args:
        negative_log_likelihood: The function minimised, summed over the
            observations, of the coordinates the covariance is for.
        point: Its minimum.
        step: The differences' step, relative to ``max(1, |x_i|)``.

    Returns:
        ``H⁻¹`` by rows: the estimate's covariance in these coordinates.

    Raises:
        EconometricsInputError: If the curvature is not positive definite.
    """

    size = len(point)
    scales = [step * max(1.0, abs(p)) for p in point]
    f = negative_log_likelihood

    def moved(changes: Mapping[int, float]) -> list[float]:
        shifted = list(point)
        for index, change in changes.items():
            shifted[index] += change
        return shifted

    centre = f(point)
    hessian = [[0.0] * size for _ in range(size)]
    for i in range(size):
        hi = scales[i]
        hessian[i][i] = (f(moved({i: hi})) - 2.0 * centre + f(moved({i: -hi}))) / (hi * hi)
        for k in range(i):
            hk = scales[k]
            value = (
                f(moved({i: hi, k: hk}))
                - f(moved({i: hi, k: -hk}))
                - f(moved({i: -hi, k: hk}))
                + f(moved({i: -hi, k: -hk}))
            ) / (4.0 * hi * hk)
            hessian[i][k] = hessian[k][i] = value
    try:
        factor = cholesky(hessian)
    except NotPositiveDefiniteError as error:
        raise EconometricsInputError(
            "The likelihood's curvature at the optimum is not positive definite: the point "
            "is no maximum to report standard errors for. Restate the start or the model."
        ) from error
    columns = [factor.solve([1.0 if j == i else 0.0 for j in range(size)]) for i in range(size)]
    return [[columns[k][i] for k in range(size)] for i in range(size)]


def curvature_standard_errors(
    negative_log_likelihood: Callable[[Sequence[float]], float],
    point: Sequence[float],
    step: float,
) -> list[float]:
    """``sqrt(diag(H⁻¹))``: :func:`curvature_covariance`'s diagonal, as standard errors.

    Raises:
        EconometricsInputError: If the curvature is not positive definite.
    """

    covariance = curvature_covariance(negative_log_likelihood, point, step)
    return [math.sqrt(covariance[i][i]) for i in range(len(point))]
