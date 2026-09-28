"""Small dense linear algebra: least squares by Householder QR, and singular values.

Until v3.11 the repository's one regression was
:func:`~alphalab.common.statistics.linear_regression` -- one regressor and an
intercept -- and neutralizing a factor against several exposures at once was
refused (ledger OFE-004) for a stated reason: a general least-squares solve
over a rank-deficient or ill-conditioned design produces residuals that look
like a result and are numerically meaningless. That reason is answered here
rather than worked around, by solving the right way and refusing the cases the
arithmetic cannot support.

Solved by QR, never by the normal equations
-------------------------------------------
Forming ``X'X`` squares the condition number: a design whose columns are
correlated at 0.9999 has ``cond(X)`` near 140 and ``cond(X'X)`` near 20,000,
and the digits lost are lost before any solver runs. :func:`least_squares`
reduces ``X`` itself with Householder reflections, which are orthogonal and so
lose nothing to conditioning beyond what the problem has.

Conditioning is measured, then refused
--------------------------------------
Every solve reports the 2-norm condition number of its design, computed from
the singular values of the triangular factor (one-sided Jacobi, which is
accurate in the smallest singular value where a Gram matrix would not be). A
design whose condition exceeds the caller's ``maximum_condition`` is refused
with :class:`IllConditionedError`, naming the number. A rank-deficient design
-- two identical exposures, a constant one beside an intercept -- has an
infinite condition and is refused the same way. Nothing is regularized: a
ridge penalty would return an answer to a different question and say nothing
about it.

What "deterministic" means here
-------------------------------
Every loop runs in a fixed order, every inner product is a
:func:`math.fsum` (exactly rounded, so independent of summation order), and the
reflection's sign is the textbook ``-sign(x0)`` choice with ``+`` for zero. The
same inputs give the same floats on every IEEE-754 machine running the same
interpreter; nothing depends on a thread count, a BLAS build or a random pivot.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from alphalab.common.exceptions import AlphaLabValidationError

__all__ = [
    "IllConditionedError",
    "LeastSquaresSolution",
    "condition_number",
    "least_squares",
    "singular_values",
]

_EPSILON = sys.float_info.epsilon

#: One-sided Jacobi converges quadratically; a well-scaled matrix settles in
#: well under ten sweeps. A bound this generous is only ever reached by a
#: defect, and reaching it is an error rather than a silently partial answer.
_MAXIMUM_SWEEPS = 64


class IllConditionedError(AlphaLabValidationError):
    """Raised when a linear system is too ill-conditioned to solve meaningfully.

    Attributes:
        condition: The 2-norm condition number that was measured -- ``inf`` for
            a rank-deficient design.
        maximum: The bound it was measured against.
    """

    def __init__(self, message: str, condition: float, maximum: float) -> None:
        super().__init__(message)
        self.condition = condition
        self.maximum = maximum


@dataclass(frozen=True, slots=True)
class LeastSquaresSolution:
    """The minimizer of ``||X b - y||``, and how far to trust it.

    Attributes:
        coefficients: ``b``, one per column of the design, in column order.
        residuals: ``y - X b`` in row order, each computed from the original
            design with an exactly rounded inner product.
        condition: The design's 2-norm condition number: the ratio of its
            largest to its smallest singular value.
        observations: Rows in the design.
    """

    coefficients: tuple[float, ...]
    residuals: tuple[float, ...]
    condition: float
    observations: int


def _require_matrix(rows: Sequence[Sequence[float]], what: str) -> tuple[int, int]:
    if not rows:
        raise AlphaLabValidationError(f"{what} needs at least one row.")
    width = len(rows[0])
    if width == 0:
        raise AlphaLabValidationError(f"{what} needs at least one column.")
    for index, row in enumerate(rows):
        if len(row) != width:
            raise AlphaLabValidationError(
                f"{what} is ragged: row 0 has {width} entries and row {index} has {len(row)}."
            )
        for column, value in enumerate(row):
            if not math.isfinite(value):
                raise AlphaLabValidationError(
                    f"{what} holds {value!r} at ({index}, {column}); a missing or unbounded "
                    "entry has to be resolved by the caller, who knows what it means."
                )
    return len(rows), width


def _columns(rows: Sequence[Sequence[float]], width: int) -> list[list[float]]:
    return [[float(row[column]) for row in rows] for column in range(width)]


def _jacobi_singular_values(columns: list[list[float]]) -> tuple[float, ...]:
    """Singular values of the matrix whose columns are given, largest first.

    Hestenes' one-sided Jacobi: rotate pairs of columns until every pair is
    orthogonal to working precision; the column norms are then the singular
    values. Mutates ``columns``.
    """

    count = len(columns)
    for _ in range(_MAXIMUM_SWEEPS):
        rotated = False
        for p in range(count - 1):
            for q in range(p + 1, count):
                first, second = columns[p], columns[q]
                alpha = math.fsum(x * x for x in first)
                beta = math.fsum(y * y for y in second)
                gamma = math.fsum(x * y for x, y in zip(first, second, strict=True))
                if gamma == 0.0 or abs(gamma) <= _EPSILON * math.sqrt(alpha) * math.sqrt(beta):
                    continue
                rotated = True
                zeta = (beta - alpha) / (2.0 * gamma)
                tangent = math.copysign(1.0, zeta) / (abs(zeta) + math.hypot(1.0, zeta))
                cosine = 1.0 / math.hypot(1.0, tangent)
                sine = cosine * tangent
                columns[p] = [cosine * x - sine * y for x, y in zip(first, second, strict=True)]
                columns[q] = [sine * x + cosine * y for x, y in zip(first, second, strict=True)]
        if not rotated:
            return tuple(sorted((math.hypot(*column) for column in columns), reverse=True))
    raise AlphaLabValidationError(
        f"The singular values did not converge within {_MAXIMUM_SWEEPS} Jacobi sweeps. A "
        "partially orthogonalized matrix would report singular values that are not."
    )


def singular_values(rows: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """The singular values of a matrix given by rows, largest first.

    ``min(rows, columns)`` values are returned; a zero means the matrix is rank
    deficient.

    Raises:
        AlphaLabValidationError: If the matrix is empty, ragged or holds a
            non-finite entry.
    """

    height, width = _require_matrix(rows, "A singular value decomposition")
    if width <= height:
        return _jacobi_singular_values(_columns(rows, width))
    # More columns than rows: the transpose has the same singular values and
    # fewer columns to rotate.
    return _jacobi_singular_values([[float(value) for value in row] for row in rows])


def condition_number(rows: Sequence[Sequence[float]]) -> float:
    """The 2-norm condition number, ``inf`` for a rank-deficient matrix.

    Raises:
        AlphaLabValidationError: As :func:`singular_values`.
    """

    values = singular_values(rows)
    if values[-1] == 0.0:
        return math.inf
    return values[0] / values[-1]


def least_squares(
    design: Sequence[Sequence[float]],
    target: Sequence[float],
    maximum_condition: float,
) -> LeastSquaresSolution:
    """Minimize ``||design @ b - target||`` by Householder QR.

    Args:
        design: The regressors, one row per observation. Include a column of
            ones for an intercept; nothing is added implicitly.
        target: One value per row.
        maximum_condition: The largest 2-norm condition number accepted. The
            solve is refused above it; see the module docstring.

    Raises:
        AlphaLabValidationError: If the design is empty, ragged or non-finite,
            the target's length differs, there are fewer rows than columns (the
            system is underdetermined and has no unique least-squares answer),
            or ``maximum_condition`` is not a finite number of at least one.
        IllConditionedError: If the design is rank deficient or its condition
            number exceeds ``maximum_condition``.
    """

    height, width = _require_matrix(design, "A least-squares design")
    if not math.isfinite(maximum_condition) or maximum_condition < 1.0:
        raise AlphaLabValidationError(
            f"maximum_condition must be a finite number of at least 1 (every matrix has a "
            f"condition of at least 1), got {maximum_condition!r}."
        )
    if len(target) != height:
        raise AlphaLabValidationError(
            f"The design has {height} rows and the target {len(target)} values."
        )
    for index, value in enumerate(target):
        if not math.isfinite(value):
            raise AlphaLabValidationError(f"The target holds {value!r} at position {index}.")
    if height < width:
        raise AlphaLabValidationError(
            f"A least-squares fit of {width} coefficients over {height} observations is "
            "underdetermined: infinitely many fit exactly, and choosing one would be choosing "
            "an answer rather than computing it."
        )

    columns = _columns(design, width)
    rhs = [float(value) for value in target]

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
        for k in range(j, width):
            column = columns[k]
            factor = (
                2.0 * math.fsum(v * c for v, c in zip(reflector, column[j:], strict=True)) / scale
            )
            for offset, v in enumerate(reflector):
                column[j + offset] -= factor * v
        factor = 2.0 * math.fsum(v * c for v, c in zip(reflector, rhs[j:], strict=True)) / scale
        for offset, v in enumerate(reflector):
            rhs[j + offset] -= factor * v

    triangular = [[columns[k][i] if k >= i else 0.0 for k in range(width)] for i in range(width)]
    values = _jacobi_singular_values(
        [[triangular[i][k] for i in range(width)] for k in range(width)]
    )
    condition = math.inf if values[-1] == 0.0 else values[0] / values[-1]
    if not condition <= maximum_condition:
        detail = "rank deficient" if math.isinf(condition) else f"conditioned at {condition:.6g}"
        raise IllConditionedError(
            f"The {height}x{width} design is {detail}, beyond the stated maximum of "
            f"{maximum_condition:g}. Its columns are (nearly) linearly dependent, so the "
            "coefficients -- and the residuals built from them -- would be decided by rounding "
            "rather than by the data.",
            condition,
            maximum_condition,
        )

    coefficients = [0.0] * width
    for i in range(width - 1, -1, -1):
        known = math.fsum(triangular[i][k] * coefficients[k] for k in range(i + 1, width))
        coefficients[i] = (rhs[i] - known) / triangular[i][i]

    residuals = tuple(
        math.fsum(
            [
                float(target[row]),
                *(-float(x) * b for x, b in zip(design[row], coefficients, strict=True)),
            ]
        )
        for row in range(height)
    )
    return LeastSquaresSolution(
        coefficients=tuple(coefficients),
        residuals=residuals,
        condition=condition,
        observations=height,
    )
