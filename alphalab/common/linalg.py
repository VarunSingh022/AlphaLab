"""Small dense linear algebra: least squares, factorizations and decompositions.

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

Factorizations with vectors (v4.3, ADR-0058)
--------------------------------------------
:func:`cholesky` is the library's one factorization of a symmetric positive
definite matrix. It was the constrained optimizer's own from v3.8 and moved here
unchanged, so every portfolio built on it is the same to the last bit.
:func:`symmetric_eigensystem` (Householder tridiagonalization and implicit QL),
:func:`singular_value_decomposition` (one-sided Jacobi, thin) and
:func:`pseudo_inverse` (built on it) return vectors as well as values.

The iterative ones take a ``tolerance`` and an iteration limit as required
arguments.
The tolerance decides the answer, so it is the caller's to choose, and no default
chooses it for them (ADR-0058 section 2). Each returns a
:class:`~alphalab.common.convergence.ConvergenceReport`, and raises
:class:`~alphalab.common.exceptions.ConvergenceError` when its criterion is not
met within its limit.

They compute with ``+ - * /``, ``sqrt`` and :func:`math.fsum` only, after scaling
the matrix by a power of two, which is exact. Their results are correctly
rounded operations in a fixed order: class 2 of ADR-0058, the same on every
IEEE-754 host and every supported CPython. Scaling the input by a power of two
scales every value by the same power, exactly, and leaves every vector as it was.

A symmetric matrix is read from its lower triangle, diagonal included, as
LAPACK's ``uplo='L'`` reads one. The upper triangle is not read, so a matrix
that is symmetric only to within rounding is taken as its lower triangle states
it.

Signs are fixed. Each eigenvector, and each right singular vector, has its
largest-magnitude component positive, the first of equal magnitudes deciding.
A left singular vector takes the sign that pairs it with its right vector.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.exceptions import AlphaLabValidationError, ConvergenceError

__all__ = [
    "CholeskyFactor",
    "IllConditionedError",
    "LeastSquaresSolution",
    "NotPositiveDefiniteError",
    "PseudoInverse",
    "SingularValueDecomposition",
    "SymmetricEigensystem",
    "cholesky",
    "condition_number",
    "least_squares",
    "pseudo_inverse",
    "singular_value_decomposition",
    "singular_values",
    "symmetric_eigensystem",
]

_EPSILON = sys.float_info.epsilon

#: One-sided Jacobi converges quadratically; a well-scaled matrix settles in
#: well under ten sweeps. A bound this generous is only ever reached by a
#: defect, and reaching it is an error rather than a silently partial answer.
_MAXIMUM_SWEEPS = 64

#: Beyond this ratio a Jacobi rotation's ``theta * theta`` would come near
#: overflow. The tangent there is ``1 / (2 theta)``, which the exact root
#: ``1 / (theta + sqrt(theta * theta + 1))`` equals to a relative ``2**-1000``.
_LARGE_RATIO = 2.0**500


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


class NotPositiveDefiniteError(AlphaLabValidationError):
    """Raised when a Cholesky pivot falls to its floor: the matrix is not positive definite.

    Attributes:
        index: The row whose pivot fell, from zero.
        pivot: That pivot: the row's diagonal entry less what the rows before
            it explain. Negative means the matrix is indefinite; at most
            :attr:`floor` means it is singular to within rounding.
        floor: ``n * eps * max(diagonal)``, the factorization's floor.
    """

    def __init__(self, message: str, index: int, pivot: float, floor: float) -> None:
        super().__init__(message)
        self.index = index
        self.pivot = pivot
        self.floor = floor


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


# --------------------------------------------------------------------------- #
# Validation shared by the factorizations
# --------------------------------------------------------------------------- #


def _require_square(matrix: Sequence[Sequence[float]], what: str) -> int:
    """The order of a square matrix whose lower triangle is finite; refuses anything else."""

    if not matrix:
        raise AlphaLabValidationError(f"{what} needs at least one row.")
    size = len(matrix)
    for index, row in enumerate(matrix):
        if len(row) != size:
            raise AlphaLabValidationError(
                f"{what} needs a square matrix: it has {size} rows and row {index} has "
                f"{len(row)} entries."
            )
        for column in range(index + 1):
            value = row[column]
            if not math.isfinite(value):
                raise AlphaLabValidationError(
                    f"{what} holds {value!r} at ({index}, {column}); a missing or unbounded "
                    "entry has to be resolved by the caller, who knows what it means."
                )
    return size


def _require_tolerance(tolerance: float, what: str) -> None:
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, int | float)
        or not 0.0 < tolerance < 1.0
    ):
        raise AlphaLabValidationError(
            f"{what} needs a tolerance strictly between 0 and 1, got {tolerance!r}. It decides "
            "the answer, so it has no default (ADR-0058)."
        )


def _require_limit(limit: int, name: str, what: str) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise AlphaLabValidationError(f"{what} needs {name}, a positive integer, got {limit!r}.")


def _scale_exponent(largest: float) -> int:
    """``e`` with ``largest * 2**-e`` in ``[0.5, 1)``; 0 for a zero matrix."""

    return 0 if largest == 0.0 else math.frexp(largest)[1]


def _unscale(value: float, exponent: int, what: str) -> float:
    try:
        return math.ldexp(value, exponent)
    except OverflowError:
        raise AlphaLabValidationError(
            f"{what} has a value beyond the floating-point range: the matrix's entries are "
            "too large for its values to be represented."
        ) from None


def _rotation(theta: float) -> tuple[float, float]:
    """``(cosine, sine)`` of the smaller rotation whose tangent solves ``t^2 + 2 theta t = 1``."""

    if abs(theta) > _LARGE_RATIO:
        tangent = 0.5 / theta
    else:
        sign = 1.0 if theta >= 0.0 else -1.0
        tangent = sign / (abs(theta) + math.sqrt(theta * theta + 1.0))
    cosine = 1.0 / math.sqrt(tangent * tangent + 1.0)
    return cosine, tangent * cosine


def _fix_sign(vector: list[float]) -> bool:
    """Make the largest-magnitude component positive, the first of equals deciding.

    Returns whether the vector was negated.
    """

    leading = max(range(len(vector)), key=lambda index: abs(vector[index]))
    if vector[leading] < 0.0:
        vector[:] = [-value for value in vector]
        return True
    return False


# --------------------------------------------------------------------------- #
# Cholesky
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CholeskyFactor:
    """``L`` with ``A = L L'``: the Cholesky factor of a symmetric positive definite ``A``.

    Made by :func:`cholesky`.

    Attributes:
        lower: ``L`` by rows, with zeros above the diagonal.
        pivots: The squares of ``L``'s diagonal, in row order. Pivot ``i`` is
            ``A[i][i]`` less what rows ``0 .. i-1`` explain: the variance of
            ``i`` conditional on the earlier rows, when ``A`` is a covariance.
    """

    lower: tuple[tuple[float, ...], ...]
    pivots: tuple[float, ...]

    @property
    def pivot_ratio(self) -> float:
        """The smallest pivot over the largest: near ``eps``, ``A`` is nearly singular.

        The square root of its reciprocal is a lower bound on the condition
        number of ``L``.
        """

        return min(self.pivots) / max(self.pivots)

    def solve(self, rhs: Sequence[float]) -> tuple[float, ...]:
        """``x`` with ``A x = rhs``: forward substitution through ``L``, then back through ``L'``.

        Every inner product is a :func:`math.fsum`.

        Raises:
            AlphaLabValidationError: If ``rhs`` has the wrong length or holds a
                non-finite value.
        """

        lower = self.lower
        size = len(lower)
        if len(rhs) != size:
            raise AlphaLabValidationError(
                f"The factor is {size} x {size} and the right-hand side has {len(rhs)} values."
            )
        values = [float(value) for value in rhs]
        for index, value in enumerate(values):
            if not math.isfinite(value):
                raise AlphaLabValidationError(
                    f"The right-hand side holds {value!r} at position {index}."
                )
        forward = [0.0] * size
        for row in range(size):
            forward[row] = (
                values[row] - math.fsum(lower[row][k] * forward[k] for k in range(row))
            ) / lower[row][row]
        solution = [0.0] * size
        for row in range(size - 1, -1, -1):
            solution[row] = (
                forward[row] - math.fsum(lower[k][row] * solution[k] for k in range(row + 1, size))
            ) / lower[row][row]
        return tuple(solution)


def cholesky(matrix: Sequence[Sequence[float]]) -> CholeskyFactor:
    """The Cholesky factor of a symmetric positive definite matrix, in natural order.

    The lower triangle is read, diagonal included; the upper is not. No pivoting
    and no regularization: a pivot at or below ``n * eps * max(diagonal)`` is
    refused rather than factored, because a factor built on it would be decided
    by rounding. The arithmetic is the constrained optimizer's of v3.8, moved
    here in v4.3 unchanged (ADR-0058): every entry is the matrix entry less an
    exactly rounded :func:`math.fsum` of the products before it.

    For a matrix that may be singular or indefinite, and the question which,
    :meth:`~alphalab.analytics.risk_model.CovarianceMatrix.definiteness` pivots
    and classifies instead.

    Raises:
        AlphaLabValidationError: If the matrix is empty, not square, or holds a
            non-finite entry in its lower triangle.
        NotPositiveDefiniteError: At the first pivot at or below the floor.
    """

    size = _require_square(matrix, "A Cholesky factorization")
    rows = [[float(matrix[row][column]) for column in range(row + 1)] for row in range(size)]
    largest = max(rows[index][index] for index in range(size))
    floor = size * _EPSILON * largest
    lower = [[0.0] * size for _ in range(size)]
    pivots: list[float] = []
    for row in range(size):
        for column in range(row + 1):
            total = rows[row][column] - math.fsum(
                lower[row][k] * lower[column][k] for k in range(column)
            )
            if row == column:
                if total <= floor:
                    raise NotPositiveDefiniteError(
                        f"The matrix is not numerically positive definite: pivot {row} is "
                        f"{total!r}, at or below the floor {floor!r} ({size} x eps x the largest "
                        "diagonal entry). It is singular to within rounding, or indefinite.",
                        row,
                        total,
                        floor,
                    )
                pivots.append(total)
                lower[row][row] = math.sqrt(total)
            else:
                lower[row][column] = total / lower[column][column]
    return CholeskyFactor(tuple(tuple(row) for row in lower), tuple(pivots))


# --------------------------------------------------------------------------- #
# The symmetric eigensystem
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SymmetricEigensystem:
    """``A = V diag(eigenvalues) V'`` for a symmetric ``A``.

    Attributes:
        eigenvalues: Largest first.
        eigenvectors: ``eigenvectors[j]`` is the unit eigenvector of
            ``eigenvalues[j]``, its largest-magnitude component positive.
            Together they are orthonormal to within rounding.
        convergence: How the iteration met its criterion.
    """

    eigenvalues: tuple[float, ...]
    eigenvectors: tuple[tuple[float, ...], ...]
    convergence: ConvergenceReport


def _pythagoras(a: float, b: float) -> float:
    """``sqrt(a^2 + b^2)`` without overflow, by ``+ - * /`` and ``sqrt`` only."""

    first, second = abs(a), abs(b)
    if first > second:
        ratio = second / first
        return first * math.sqrt(1.0 + ratio * ratio)
    if second == 0.0:
        return 0.0
    ratio = first / second
    return second * math.sqrt(1.0 + ratio * ratio)


def _tridiagonalize(
    work: list[list[float]],
) -> tuple[list[float], list[float], list[list[float]]]:
    """Householder reduction ``A = Q T Q'``: ``T``'s diagonal and sub-diagonal, and ``Q'`` by rows.

    Step ``k`` reflects column ``k`` below the sub-diagonal onto it and applies
    the reflection to the trailing block from both sides, as the rank-two update
    ``A - (v w' + w v')``. That form keeps the block exactly symmetric, since
    ``+`` and ``*`` commute in IEEE-754. ``Q`` is accumulated backwards, where
    each reflection touches only its own trailing block. Mutates ``work``.
    """

    size = len(work)
    off = [0.0] * size
    reflections: list[tuple[int, list[float], float]] = []
    for k in range(size - 2):
        start = k + 1
        column = work[k][start:]
        norm = math.sqrt(math.fsum(x * x for x in column))
        if norm == 0.0:
            continue
        alpha = -norm if column[0] >= 0.0 else norm
        vector = list(column)
        vector[0] -= alpha
        scale = math.fsum(x * x for x in vector)
        if scale == 0.0:
            continue
        beta = 2.0 / scale
        product = [
            beta * math.fsum(a * x for a, x in zip(work[start + i][start:], vector, strict=True))
            for i in range(len(vector))
        ]
        half = 0.5 * beta * math.fsum(p * x for p, x in zip(product, vector, strict=True))
        update = [p - half * x for p, x in zip(product, vector, strict=True)]
        for i, (vi, wi) in enumerate(zip(vector, update, strict=True)):
            row = work[start + i]
            row[start:] = [
                a - (vi * wj + wi * vj)
                for a, vj, wj in zip(row[start:], vector, update, strict=True)
            ]
        work[k][start] = work[start][k] = alpha
        reflections.append((start, vector, beta))
    diagonal = [work[i][i] for i in range(size)]
    for i in range(size - 1):
        off[i] = work[i + 1][i]

    basis = [[1.0 if row == column else 0.0 for column in range(size)] for row in range(size)]
    for start, vector, beta in reversed(reflections):
        width = len(vector)
        projection = [
            math.fsum(vector[i] * basis[start + i][start + j] for i in range(width))
            for j in range(width)
        ]
        for i in range(width):
            row = basis[start + i]
            factor = beta * vector[i]
            row[start:] = [q - factor * u for q, u in zip(row[start:], projection, strict=True)]
    return diagonal, off, [list(column) for column in zip(*basis, strict=True)]


def _implicit_ql(
    diagonal: list[float],
    off: list[float],
    vectors: list[list[float]],
    tolerance: float,
    max_iterations: int,
) -> tuple[int, float]:
    """Implicit QL with shifts on a symmetric tridiagonal matrix (Bowdler et al., ``tql2``).

    ``off[i]`` couples ``diagonal[i]`` and ``diagonal[i + 1]``, and ``off[-1]`` is
    zero. The matrix splits wherever ``|off[m]| <= tolerance * (|d[m]| + |d[m+1]|)``;
    an eigenvalue is found when its block is one by one. Each QL step's
    rotations are applied to ``vectors``, whose rows become the eigenvectors.

    Returns the most iterations any one eigenvalue took, and the largest value
    of the criterion's ratio at which the matrix was split.
    """

    size = len(diagonal)
    most = 0
    reached = 0.0
    for low in range(size):
        iterations = 0
        while True:
            high = low
            while high < size - 1:
                magnitude = abs(diagonal[high]) + abs(diagonal[high + 1])
                if abs(off[high]) <= tolerance * magnitude:
                    if off[high] != 0.0:
                        reached = max(reached, abs(off[high]) / magnitude)
                    break
                high += 1
            if high == low:
                break
            if iterations == max_iterations:
                magnitude = abs(diagonal[low]) + abs(diagonal[low + 1])
                measure = abs(off[low]) / magnitude if magnitude > 0.0 else math.inf
                raise ConvergenceError(
                    "the implicit QL eigensystem", tolerance, max_iterations, measure
                )
            iterations += 1
            g = (diagonal[low + 1] - diagonal[low]) / (2.0 * off[low])
            r = _pythagoras(g, 1.0)
            g = diagonal[high] - diagonal[low] + off[low] / (g + (r if g >= 0.0 else -r))
            sine = cosine = 1.0
            shift = 0.0
            restarted = False
            for i in range(high - 1, low - 1, -1):
                f = sine * off[i]
                b = cosine * off[i]
                r = _pythagoras(f, g)
                off[i + 1] = r
                if r == 0.0:
                    # Underflow: the matrix split at i + 1. Begin again from low.
                    diagonal[i + 1] -= shift
                    off[high] = 0.0
                    restarted = True
                    break
                sine = f / r
                cosine = g / r
                g = diagonal[i + 1] - shift
                r = (diagonal[i] - g) * sine + 2.0 * cosine * b
                shift = sine * r
                diagonal[i + 1] = g + shift
                g = cosine * r - b
                first, second = vectors[i], vectors[i + 1]
                vectors[i + 1] = [sine * x + cosine * y for x, y in zip(first, second, strict=True)]
                vectors[i] = [cosine * x - sine * y for x, y in zip(first, second, strict=True)]
            if restarted:
                continue
            diagonal[low] -= shift
            off[low] = g
            off[high] = 0.0
        most = max(most, iterations)
    return most, reached


def symmetric_eigensystem(
    matrix: Sequence[Sequence[float]],
    *,
    tolerance: float,
    max_iterations: int,
) -> SymmetricEigensystem:
    """Eigenvalues and eigenvectors of a symmetric matrix.

    Householder reflections reduce the matrix to tridiagonal form, and the
    implicit QL algorithm with shifts diagonalizes that (Bowdler, Martin,
    Reinsch and Wilkinson's ``tql2``), the reflections and rotations
    accumulated into the eigenvectors. Both stages are backward stable, so the
    eigenvectors are orthonormal to working precision whatever the eigenvalues'
    spacing.

    The tolerance is the splitting criterion. A sub-diagonal entry at most
    ``tolerance`` times the sum of its two diagonal neighbours' magnitudes is
    taken as zero. That perturbs the matrix by at most ``2 * tolerance *
    ||A||_2``, so by Weyl's inequality each eigenvalue is within that of the
    exact one, beyond rounding of a modest multiple of ``n * eps * ||A||``. QL
    converges cubically, so a tolerance near ``eps`` costs little more than a
    loose one. An eigenvector's accuracy is that bound over the gap to the
    nearest other eigenvalue, as for every method.

    Only the lower triangle is read, diagonal included.

    Args:
        matrix: A symmetric matrix, by rows.
        tolerance: The criterion, strictly between 0 and 1. Required (ADR-0058).
        max_iterations: The QL iterations allowed for any one eigenvalue.
            Required. Three or four is typical, thirty generous.

    Raises:
        AlphaLabValidationError: If the matrix is empty, not square or holds a
            non-finite entry in its lower triangle, or an argument is out of
            range.
        ConvergenceError: If an eigenvalue does not split off within
            ``max_iterations``.
    """

    what = "A symmetric eigensystem"
    size = _require_square(matrix, what)
    _require_tolerance(tolerance, what)
    _require_limit(max_iterations, "max_iterations", what)

    exponent = _scale_exponent(
        max(abs(float(matrix[row][column])) for row in range(size) for column in range(row + 1))
    )
    work = [[0.0] * size for _ in range(size)]
    for row in range(size):
        for column in range(row + 1):
            value = math.ldexp(float(matrix[row][column]), -exponent)
            work[row][column] = value
            work[column][row] = value

    diagonal, off, vectors = _tridiagonalize(work)
    iterations, reached = _implicit_ql(diagonal, off, vectors, tolerance, max_iterations)

    order = sorted(range(size), key=lambda index: diagonal[index], reverse=True)
    eigenvectors = []
    for index in order:
        vector = vectors[index]
        _fix_sign(vector)
        eigenvectors.append(tuple(vector))
    return SymmetricEigensystem(
        eigenvalues=tuple(_unscale(diagonal[index], exponent, what) for index in order),
        eigenvectors=tuple(eigenvectors),
        convergence=ConvergenceReport(
            method="the implicit QL eigensystem",
            criterion=(
                "sub-diagonal entry over the sum of its diagonal neighbours' magnitudes, "
                "at each split"
            ),
            tolerance=float(tolerance),
            limit=max_iterations,
            iterations=iterations,
            reached=reached,
        ),
    )


# --------------------------------------------------------------------------- #
# The singular value decomposition
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SingularValueDecomposition:
    """The thin decomposition ``A = U diag(singular_values) V'`` of an ``m x n`` matrix.

    With ``k = min(m, n)``:

    Attributes:
        singular_values: ``k`` values, largest first, none negative.
        left_vectors: ``k`` orthonormal vectors of length ``m``, the columns of
            ``U``. A zero singular value's is a completion: a unit vector
            orthogonal to the others, which ``A`` does not determine.
        right_vectors: ``k`` orthonormal vectors of length ``n``, the columns of
            ``V``, each with its largest-magnitude component positive.
        convergence: How the iteration met its criterion.
    """

    singular_values: tuple[float, ...]
    left_vectors: tuple[tuple[float, ...], ...]
    right_vectors: tuple[tuple[float, ...], ...]
    convergence: ConvergenceReport


def _cosine(first: list[float], second: list[float]) -> tuple[float, float, float, float]:
    """``(alpha, beta, gamma, |cos|)``: squared norms, inner product, and their cosine."""

    alpha = math.fsum(x * x for x in first)
    beta = math.fsum(y * y for y in second)
    gamma = math.fsum(x * y for x, y in zip(first, second, strict=True))
    if gamma == 0.0 or alpha == 0.0 or beta == 0.0:
        return alpha, beta, gamma, 0.0
    return alpha, beta, gamma, abs(gamma) / (math.sqrt(alpha) * math.sqrt(beta))


def _hestenes_sweep(
    vectors: list[list[float]], rotations: list[list[float]], tolerance: float
) -> tuple[bool, float]:
    """One one-sided Jacobi sweep; ``(rotated, worst cosine among the pairs left alone)``."""

    count = len(vectors)
    rotated = False
    worst = 0.0
    for p in range(count - 1):
        for q in range(p + 1, count):
            first, second = vectors[p], vectors[q]
            alpha, beta, gamma, cosine_measure = _cosine(first, second)
            if cosine_measure <= tolerance:
                worst = max(worst, cosine_measure)
                continue
            rotated = True
            cosine, sine = _rotation((beta - alpha) / (2.0 * gamma))
            vectors[p] = [cosine * x - sine * y for x, y in zip(first, second, strict=True)]
            vectors[q] = [sine * x + cosine * y for x, y in zip(first, second, strict=True)]
            turn_p, turn_q = rotations[p], rotations[q]
            rotations[p] = [cosine * x - sine * y for x, y in zip(turn_p, turn_q, strict=True)]
            rotations[q] = [sine * x + cosine * y for x, y in zip(turn_p, turn_q, strict=True)]
    return rotated, worst


def _worst_cosine(vectors: list[list[float]]) -> float:
    count = len(vectors)
    return max(
        (_cosine(vectors[p], vectors[q])[3] for p in range(count - 1) for q in range(p + 1, count)),
        default=0.0,
    )


def _complete(basis: list[list[float]], dimension: int) -> list[float]:
    """A unit vector orthogonal to ``basis``: the standard basis vector it leaves most of.

    Gram-Schmidt twice, which restores orthogonality to working precision.
    """

    leftover = [
        math.fsum([1.0, *(-(vector[index] * vector[index]) for vector in basis)])
        for index in range(dimension)
    ]
    chosen = max(range(dimension), key=lambda index: leftover[index])
    candidate = [1.0 if index == chosen else 0.0 for index in range(dimension)]
    for _ in range(2):
        for vector in basis:
            overlap = math.fsum(v * c for v, c in zip(vector, candidate, strict=True))
            candidate = [c - overlap * v for v, c in zip(vector, candidate, strict=True)]
    norm = math.sqrt(math.fsum(c * c for c in candidate))
    return [c / norm for c in candidate]


def singular_value_decomposition(
    rows: Sequence[Sequence[float]],
    *,
    tolerance: float,
    max_sweeps: int,
) -> SingularValueDecomposition:
    """The thin singular value decomposition, by one-sided (Hestenes) Jacobi.

    Pairs of columns are rotated until every pair's cosine is at most
    ``tolerance``. The columns' norms are then the singular values, and the
    rotations accumulated are ``V``. A matrix with more columns than rows is
    decomposed through its transpose. At the criterion, the computed ``U`` is
    orthonormal to within ``(k - 1) * tolerance``, so each singular value is
    within that factor of the exact one, relatively, beyond the rounding of
    the rotations. The smallest useful tolerance is a small multiple of
    ``m * eps``; one far below it is never met, and the call raises.

    Args:
        rows: The matrix, by rows.
        tolerance: The criterion, strictly between 0 and 1. Required (ADR-0058).
        max_sweeps: The sweeps allowed. Required.

    Raises:
        AlphaLabValidationError: If the matrix is empty, ragged or holds a
            non-finite entry, or an argument is out of range.
        ConvergenceError: If the criterion is not met within ``max_sweeps``.
    """

    what = "A singular value decomposition"
    height, width = _require_matrix(rows, what)
    _require_tolerance(tolerance, what)
    _require_limit(max_sweeps, "max_sweeps", what)

    exponent = _scale_exponent(max(abs(float(value)) for row in rows for value in row))
    transposed = width > height
    if transposed:
        vectors = [[math.ldexp(float(value), -exponent) for value in row] for row in rows]
    else:
        vectors = [
            [math.ldexp(float(row[column]), -exponent) for row in rows] for column in range(width)
        ]
    count = len(vectors)
    rotations = [[1.0 if row == column else 0.0 for column in range(count)] for row in range(count)]

    sweeps = 0
    method = "the one-sided Jacobi singular value decomposition"
    while True:
        if sweeps == max_sweeps:
            worst = _worst_cosine(vectors)
            if worst <= tolerance:
                break
            raise ConvergenceError(method, tolerance, max_sweeps, worst)
        rotated, worst = _hestenes_sweep(vectors, rotations, tolerance)
        if not rotated:
            break
        sweeps += 1

    norms = [math.sqrt(math.fsum(x * x for x in vector)) for vector in vectors]
    order = sorted(range(count), key=lambda index: norms[index], reverse=True)
    dimension = len(vectors[0])
    directions: list[list[float]] = []
    for index in order:
        if norms[index] > 0.0:
            directions.append([x / norms[index] for x in vectors[index]])
    for _ in range(count - len(directions)):
        directions.append(_complete(directions, dimension))
    accumulated = [rotations[index] for index in order]

    left, right = (accumulated, directions) if transposed else (directions, accumulated)
    for position in range(count):
        if _fix_sign(right[position]):
            left[position] = [-value for value in left[position]]
    return SingularValueDecomposition(
        singular_values=tuple(_unscale(norms[index], exponent, what) for index in order),
        left_vectors=tuple(tuple(vector) for vector in left),
        right_vectors=tuple(tuple(vector) for vector in right),
        convergence=ConvergenceReport(
            method=method,
            criterion="largest cosine between two columns",
            tolerance=float(tolerance),
            limit=max_sweeps,
            iterations=sweeps,
            reached=worst,
        ),
    )


# --------------------------------------------------------------------------- #
# The pseudo-inverse
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PseudoInverse:
    """The Moore-Penrose pseudo-inverse of an ``m x n`` matrix, and what it kept.

    Attributes:
        matrix: ``A+``, ``n x m``, by rows.
        rank: The singular values inverted: those above :attr:`threshold`.
        threshold: ``relative_cutoff`` times the largest singular value. A
            singular value at or below it is taken as zero.
        decomposition: The decomposition it was built from.
    """

    matrix: tuple[tuple[float, ...], ...]
    rank: int
    threshold: float
    decomposition: SingularValueDecomposition


def pseudo_inverse(
    rows: Sequence[Sequence[float]],
    *,
    relative_cutoff: float,
    tolerance: float,
    max_sweeps: int,
) -> PseudoInverse:
    """``A+ = V diag(1 / sigma) U'`` over the singular values above a cutoff.

    Which singular values count as zero decides the answer, so the cutoff is
    required, as the decomposition's tolerance is. ``max(m, n) * eps`` is the
    usual choice for a matrix known only to working precision; a matrix
    measured with noise needs one at the noise's level.

    Args:
        rows: The matrix, by rows.
        relative_cutoff: In ``[0, 1)``: singular values at or below this
            fraction of the largest are taken as zero.
        tolerance: The decomposition's criterion; see
            :func:`singular_value_decomposition`.
        max_sweeps: The decomposition's sweeps allowed.

    Raises:
        AlphaLabValidationError: As :func:`singular_value_decomposition`, if
            the cutoff is out of range, or if inverting a singular value kept
            leaves the floating-point range.
        ConvergenceError: As :func:`singular_value_decomposition`.
    """

    if (
        isinstance(relative_cutoff, bool)
        or not isinstance(relative_cutoff, int | float)
        or not 0.0 <= relative_cutoff < 1.0
    ):
        raise AlphaLabValidationError(
            f"A pseudo-inverse needs a relative_cutoff in [0, 1), got {relative_cutoff!r}. "
            "It decides which singular values are zero, so it has no default (ADR-0058)."
        )
    decomposition = singular_value_decomposition(rows, tolerance=tolerance, max_sweeps=max_sweeps)
    values = decomposition.singular_values
    threshold = relative_cutoff * values[0]
    rank = sum(1 for value in values if value > threshold)
    scaled = [
        [component / values[k] for component in decomposition.left_vectors[k]] for k in range(rank)
    ]
    if not all(math.isfinite(component) for vector in scaled for component in vector):
        raise AlphaLabValidationError(
            f"Inverting the singular values above {threshold!r} leaves the floating-point "
            "range; a larger relative_cutoff would leave the smallest of them out."
        )
    right = decomposition.right_vectors
    height, width = len(rows), len(rows[0])
    matrix = tuple(
        tuple(math.fsum(right[k][i] * scaled[k][j] for k in range(rank)) for j in range(height))
        for i in range(width)
    )
    return PseudoInverse(matrix, rank, threshold, decomposition)
