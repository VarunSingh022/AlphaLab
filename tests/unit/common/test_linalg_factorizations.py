"""Cholesky, the symmetric eigensystem, the thin SVD and the pseudo-inverse (v4.3, ADR-0058).

Each is held three ways, as ADR-0058 section 5 asks:

- **Exact oracles.** Closed forms evaluated in 60-digit ``Decimal`` or exact
  ``Fraction``. A symmetric tridiagonal matrix's eigenvalues come from Sturm
  sequence bisection in ``Decimal``, which needs no floating point at all; it
  holds the eigenvalues of Wilkinson's ``W21+`` -- two of them 1e-13
  apart -- and of the second-difference matrix.
- **Error bounds**, not flat tolerances. The eigensystem is Householder
  tridiagonalization and implicit QL. Each of its ``n - 2`` reflections and
  each stage of its rotations adds a backward error of a small multiple of
  ``n * eps * ||A||`` (Higham, *Accuracy and Stability of Numerical
  Algorithms*, Lemmas 19.3 and 19.9), so the whole stays within ``8 n^2 eps
  ||A||_F``; dropping the sub-diagonal entries the criterion accepts adds
  ``2 * tolerance * ||A||``, and Weyl's inequality puts every eigenvalue
  within the sum. The decomposition is one-sided Jacobi: a sweep's rotations
  touch each column ``2(n - 1)`` times, so ``k`` sweeps leave a backward error
  within ``8 n (k + 1) eps ||A||_F`` (Lemma 19.9). Cholesky's bounds are
  Higham's Theorems 10.3 and 10.4. A worst-case bound is loose, so the
  eigenvalues are also held to the exact a-posteriori bound of
  Krylov-Weinstein, computed in ``Fraction``.
- **Pinned values**, as hex, for the cross-host identity CI checks on Linux
  and macOS under every supported CPython: each method computes with ``+ - *
  /``, ``sqrt`` and ``fsum`` only, so the same input gives the same bits.
"""

from __future__ import annotations

import math
import random
import sys
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.exceptions import AlphaLabValidationError, ConvergenceError
from alphalab.common.linalg import (
    CholeskyFactor,
    NotPositiveDefiniteError,
    PseudoInverse,
    SingularValueDecomposition,
    SymmetricEigensystem,
    cholesky,
    least_squares,
    pseudo_inverse,
    singular_value_decomposition,
    singular_values,
    symmetric_eigensystem,
)

EPS = sys.float_info.epsilon
TOLERANCE = 1e-15
SWEEPS = 60
ITERATIONS = 30

Matrix = list[list[float]]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _frobenius(matrix: Matrix) -> float:
    return math.sqrt(math.fsum(value * value for row in matrix for value in row))


def _dot(first: tuple[float, ...] | list[float], second: tuple[float, ...] | list[float]) -> float:
    return math.fsum(x * y for x, y in zip(first, second, strict=True))


def _orthogonality(vectors: tuple[tuple[float, ...], ...]) -> float:
    """``max |V'V - I|`` over the vectors given."""

    return max(
        abs(_dot(vectors[i], vectors[j]) - (1.0 if i == j else 0.0))
        for i in range(len(vectors))
        for j in range(len(vectors))
    )


def _symmetric(rng: random.Random, size: int) -> Matrix:
    draws = [[rng.uniform(-1.0, 1.0) for _ in range(size)] for _ in range(size)]
    return [[(draws[i][j] + draws[j][i]) / 2.0 for j in range(size)] for i in range(size)]


def _rectangular(rng: random.Random, height: int, width: int) -> Matrix:
    return [[rng.uniform(-1.0, 1.0) for _ in range(width)] for _ in range(height)]


def _eigen_bound(size: int, tolerance: float) -> float:
    """The eigensystem's relative bound of the module docstring: ``2 tol + 8 n^2 eps``."""

    return 2.0 * tolerance + 8.0 * size * size * EPS


def _jacobi_bound(size: int, sweeps: int, tolerance: float) -> float:
    """The relative bound of the module docstring: tolerance plus ``8 n (k + 1) eps``."""

    return tolerance + 8.0 * size * (sweeps + 1) * EPS


def _sturm_eigenvalues(diagonal: list[int], off: list[int]) -> list[Decimal]:
    """Every eigenvalue of a symmetric tridiagonal integer matrix, by Sturm bisection.

    The count of negative terms in ``d_1 = a_1 - x``, ``d_i = a_i - x -
    b_{i-1}^2 / d_{i-1}`` is the number of eigenvalues below ``x``. In 60-digit
    Decimal, bisected to ``1e-40``: an oracle independent of floating point.
    """

    size = len(diagonal)
    with localcontext() as context:
        context.prec = 60
        tiny = Decimal("1e-50")

        def below(x: Decimal) -> int:
            count = 0
            d = Decimal(diagonal[0]) - x
            for i in range(size):
                if i:
                    d = Decimal(diagonal[i]) - x - Decimal(off[i - 1] ** 2) / d
                if d == 0:
                    d = tiny
                if d < 0:
                    count += 1
            return count

        radius = max(abs(a) for a in diagonal) + 2 * max((abs(b) for b in off), default=0) + 1
        values = []
        for index in range(size):  # the (index+1)-th smallest
            low, high = Decimal(-radius), Decimal(radius)
            while high - low > Decimal("1e-40"):
                middle = (low + high) / 2
                if below(middle) > index:
                    high = middle
                else:
                    low = middle
            values.append((low + high) / 2)
        return sorted(values, reverse=True)


def _hex(values: tuple[float, ...]) -> list[str]:
    return [value.hex() for value in values]


# --------------------------------------------------------------------------- #
# Cholesky
# --------------------------------------------------------------------------- #


def test_the_classic_example_factors_exactly() -> None:
    factor = cholesky([[4.0, 12.0, -16.0], [12.0, 37.0, -43.0], [-16.0, -43.0, 98.0]])

    assert factor.lower == ((2.0, 0.0, 0.0), (6.0, 1.0, 0.0), (-8.0, 5.0, 3.0))
    assert factor.pivots == (4.0, 1.0, 9.0)
    assert factor.pivot_ratio == 1.0 / 9.0
    assert factor.solve([-20.0, -43.0, 192.0]) == (1.0, 2.0, 3.0)


@pytest.mark.parametrize("size", [1, 2, 4, 7, 12])
def test_a_factor_rebuilds_its_matrix_within_higham_10_3(size: int) -> None:
    rng = random.Random(size)
    draws = _rectangular(rng, size + 5, size)
    matrix = [
        [math.fsum(row[i] * row[j] for row in draws) for j in range(size)] for i in range(size)
    ]
    lower = cholesky(matrix).lower

    for i in range(size):
        for j in range(size):
            rebuilt = _dot(lower[i], lower[j])
            magnitude = math.fsum(abs(x * y) for x, y in zip(lower[i], lower[j], strict=True))
            assert abs(rebuilt - matrix[i][j]) <= (size + 2) * EPS * magnitude


@pytest.mark.parametrize("size", [1, 3, 6, 10])
def test_a_solve_has_a_small_residual_within_higham_10_4(size: int) -> None:
    rng = random.Random(100 + size)
    draws = _rectangular(rng, size + 3, size)
    matrix = [
        [
            math.fsum(row[i] * row[j] for row in draws) + (0.1 if i == j else 0.0)
            for j in range(size)
        ]
        for i in range(size)
    ]
    rhs = [rng.uniform(-5.0, 5.0) for _ in range(size)]
    factor = cholesky(matrix)
    solution = factor.solve(rhs)
    lower = factor.lower
    absolute = [
        [math.fsum(abs(lower[i][k] * lower[j][k]) for k in range(size)) for j in range(size)]
        for i in range(size)
    ]

    for i in range(size):
        residual = math.fsum([*(matrix[i][j] * solution[j] for j in range(size)), -rhs[i]])
        bound = (3 * size + 2) * EPS * math.fsum(
            absolute[i][j] * abs(solution[j]) for j in range(size)
        ) + EPS * abs(rhs[i])
        assert abs(residual) <= bound


def test_only_the_lower_triangle_is_read() -> None:
    symmetric = [[4.0, 1.0, 0.5], [1.0, 3.0, 0.25], [0.5, 0.25, 2.0]]
    lower_only = [[4.0, math.nan, math.inf], [1.0, 3.0, -7.0], [0.5, 0.25, 2.0]]

    assert cholesky(lower_only) == cholesky(symmetric)


@pytest.mark.parametrize(
    ("matrix", "index", "pivot"),
    [
        ([[1.0, 2.0], [2.0, 1.0]], 1, -3.0),
        ([[1.0, 1.0], [1.0, 1.0]], 1, 0.0),
        ([[-1.0, 0.0], [0.0, 1.0]], 0, -1.0),
        ([[0.0]], 0, 0.0),
    ],
)
def test_a_matrix_that_is_not_positive_definite_is_refused_at_its_pivot(
    matrix: Matrix, index: int, pivot: float
) -> None:
    with pytest.raises(NotPositiveDefiniteError) as raised:
        cholesky(matrix)

    assert raised.value.index == index
    assert raised.value.pivot == pivot
    largest = max(matrix[i][i] for i in range(len(matrix)))
    assert raised.value.floor == len(matrix) * EPS * largest
    assert isinstance(raised.value, AlphaLabValidationError)


@pytest.mark.parametrize(
    "matrix",
    [[], [[1.0, 0.0]], [[1.0, 0.0], [0.0]], [[math.nan]], [[1.0, 0.0], [math.inf, 1.0]]],
)
def test_a_malformed_matrix_is_refused(matrix: Matrix) -> None:
    with pytest.raises(AlphaLabValidationError):
        cholesky(matrix)


@pytest.mark.parametrize("rhs", [[1.0], [1.0, 2.0, 3.0], [1.0, math.nan]])
def test_a_malformed_right_hand_side_is_refused(rhs: list[float]) -> None:
    factor = cholesky([[2.0, 0.0], [0.0, 2.0]])
    with pytest.raises(AlphaLabValidationError):
        factor.solve(rhs)


# --------------------------------------------------------------------------- #
# The symmetric eigensystem
# --------------------------------------------------------------------------- #


def test_a_diagonal_matrix_needs_no_iteration() -> None:
    system = symmetric_eigensystem(
        [[1.0, 0.0, 0.0], [0.0, -2.0, 0.0], [0.0, 0.0, 5.0]], tolerance=TOLERANCE, max_iterations=1
    )

    assert system.eigenvalues == (5.0, 1.0, -2.0)
    assert system.eigenvectors == ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    assert system.convergence.iterations == 0
    assert system.convergence.reached == 0.0


def test_the_two_by_two_eigensystem_meets_its_closed_form() -> None:
    system = symmetric_eigensystem([[2.0, 1.0], [1.0, 2.0]], tolerance=TOLERANCE, max_iterations=5)
    half = math.sqrt(0.5)

    assert system.eigenvalues == pytest.approx((3.0, 1.0), abs=4 * EPS)
    assert system.eigenvectors[0] == pytest.approx((half, half), abs=4 * EPS)
    # Equal magnitudes: the first component decides the sign.
    assert system.eigenvectors[1] == pytest.approx((half, -half), abs=4 * EPS)


@pytest.mark.parametrize("seed", range(12))
def test_two_by_two_eigenvalues_meet_the_closed_form(seed: int) -> None:
    rng = random.Random(seed)
    a, b, c = (rng.uniform(-10.0, 10.0) * 10.0 ** rng.randint(-4, 4) for _ in range(3))
    system = symmetric_eigensystem([[a, b], [b, c]], tolerance=TOLERANCE, max_iterations=10)
    with localcontext() as context:
        context.prec = 60
        middle = (Decimal(a) + Decimal(c)) / 2
        radius = (((Decimal(a) - Decimal(c)) / 2) ** 2 + Decimal(b) ** 2).sqrt()
        exact = (middle + radius, middle - radius)
    scale = _frobenius([[a, b], [b, c]])
    bound = _eigen_bound(2, TOLERANCE) * scale

    for computed, value in zip(system.eigenvalues, exact, strict=True):
        assert abs(Decimal(computed) - value) <= Decimal(bound)


@pytest.mark.parametrize(
    ("name", "diagonal", "off"),
    [
        ("second difference, n=10", [2] * 10, [-1] * 9),
        ("Wilkinson W21+", [abs(10 - i) for i in range(21)], [1] * 20),
        ("Wilkinson W21-", [10 - i for i in range(21)], [1] * 20),
    ],
)
def test_tridiagonal_eigenvalues_meet_sturm_bisection(
    name: str, diagonal: list[int], off: list[int]
) -> None:
    size = len(diagonal)
    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        matrix[i][i] = float(diagonal[i])
        if i:
            matrix[i][i - 1] = matrix[i - 1][i] = float(off[i - 1])
    system = symmetric_eigensystem(matrix, tolerance=TOLERANCE, max_iterations=ITERATIONS)
    exact = _sturm_eigenvalues(diagonal, off)
    bound = Decimal(_eigen_bound(size, TOLERANCE)) * Decimal(_frobenius(matrix))

    for computed, value in zip(system.eigenvalues, exact, strict=True):
        assert abs(Decimal(computed) - value) <= bound, name


@pytest.mark.parametrize(
    ("diagonal", "off"),
    [([abs(10 - i) for i in range(21)], [1] * 20), ([2] * 10, [-1] * 9)],
    ids=["Wilkinson W21+", "second difference, n=10"],
)
def test_each_eigenvalue_is_within_its_exact_residual_of_the_truth(
    diagonal: list[int], off: list[int]
) -> None:
    """The a-posteriori bound, rigorous and tight: Krylov-Weinstein, in exact arithmetic.

    For a symmetric ``A`` and any ``v`` and ``l``, an eigenvalue of ``A`` lies
    within ``||A v - l v|| / ||v||``. The residual is computed in ``Fraction``,
    so the bound is exact. W21+'s two largest eigenvalues are 1e-13 apart and
    the bound separates them; the a-priori bound above cannot.
    """

    size = len(diagonal)
    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        matrix[i][i] = float(diagonal[i])
        if i:
            matrix[i][i - 1] = matrix[i - 1][i] = float(off[i - 1])
    system = symmetric_eigensystem(matrix, tolerance=TOLERANCE, max_iterations=ITERATIONS)
    exact = _sturm_eigenvalues(diagonal, off)

    radii = []
    for value, vector in zip(system.eigenvalues, system.eigenvectors, strict=True):
        components = [Fraction(component) for component in vector]
        residual = [
            sum((Fraction(int(matrix[i][j])) * components[j] for j in range(size)), Fraction(0))
            - Fraction(value) * components[i]
            for i in range(size)
        ]
        squared = sum((r * r for r in residual), Fraction(0)) / sum(
            (c * c for c in components), Fraction(0)
        )
        with localcontext() as context:
            context.prec = 60
            radii.append((Decimal(squared.numerator) / Decimal(squared.denominator)).sqrt())
    # Disjoint intervals each hold one eigenvalue, so they pair with the truth in order.
    for upper, lower, a, b in zip(
        system.eigenvalues, system.eigenvalues[1:], radii, radii[1:], strict=False
    ):
        assert Decimal(upper) - Decimal(lower) > a + b
    for value, radius, truth in zip(system.eigenvalues, radii, exact, strict=True):
        assert abs(Decimal(value) - truth) <= radius + Decimal("1e-39")
        assert radius <= Decimal(32 * EPS) * Decimal(_frobenius(matrix))


@pytest.mark.parametrize("size", [3, 6, 11, 20])
def test_a_random_symmetric_matrix_is_diagonalized_within_the_bound(size: int) -> None:
    rng = random.Random(size * 31)
    matrix = _symmetric(rng, size)
    system = symmetric_eigensystem(matrix, tolerance=TOLERANCE, max_iterations=ITERATIONS)
    values, vectors = system.eigenvalues, system.eigenvectors
    norm = _frobenius(matrix)

    assert list(values) == sorted(values, reverse=True)
    assert _orthogonality(vectors) <= 8.0 * size * size * EPS
    bound = _eigen_bound(size, TOLERANCE) * norm
    for value, vector in zip(values, vectors, strict=True):
        for i in range(size):
            residual = math.fsum(
                [*(matrix[i][j] * vector[j] for j in range(size)), -value * vector[i]]
            )
            assert abs(residual) <= bound
    trace = math.fsum(matrix[i][i] for i in range(size))
    assert abs(math.fsum(values) - trace) <= size * bound
    assert abs(math.sqrt(math.fsum(v * v for v in values)) - norm) <= size * bound


def test_each_eigenvector_has_its_largest_component_positive() -> None:
    rng = random.Random(77)
    system = symmetric_eigensystem(
        _symmetric(rng, 9), tolerance=TOLERANCE, max_iterations=ITERATIONS
    )

    for vector in system.eigenvectors:
        leading = max(range(len(vector)), key=lambda index: abs(vector[index]))
        assert vector[leading] > 0.0


def test_repeated_and_zero_eigenvalues_keep_an_orthonormal_basis() -> None:
    # u u' + v v' with orthogonal u and v: eigenvalues |u|^2, |v|^2 and zeros.
    u = [1.0, 1.0, 0.0, 0.0]
    v = [0.0, 0.0, 1.0, 1.0]
    matrix = [[u[i] * u[j] + v[i] * v[j] for j in range(4)] for i in range(4)]
    system = symmetric_eigensystem(matrix, tolerance=TOLERANCE, max_iterations=ITERATIONS)

    assert system.eigenvalues == pytest.approx((2.0, 2.0, 0.0, 0.0), abs=8 * EPS)
    assert _orthogonality(system.eigenvectors) <= 64 * EPS


def test_an_indefinite_matrix_has_negative_eigenvalues() -> None:
    system = symmetric_eigensystem([[0.0, 1.0], [1.0, 0.0]], tolerance=TOLERANCE, max_iterations=5)

    assert system.eigenvalues == pytest.approx((1.0, -1.0), abs=EPS)


@pytest.mark.parametrize("power", [-700, -60, 60, 700])
def test_scaling_by_a_power_of_two_scales_the_eigenvalues_exactly(power: int) -> None:
    rng = random.Random(5)
    matrix = _symmetric(rng, 6)
    scaled = [[math.ldexp(value, power) for value in row] for row in matrix]

    plain = symmetric_eigensystem(matrix, tolerance=TOLERANCE, max_iterations=ITERATIONS)
    other = symmetric_eigensystem(scaled, tolerance=TOLERANCE, max_iterations=ITERATIONS)

    assert other.eigenvalues == tuple(math.ldexp(value, power) for value in plain.eigenvalues)
    assert other.eigenvectors == plain.eigenvectors


def test_only_the_lower_triangle_of_a_symmetric_matrix_is_read() -> None:
    rng = random.Random(9)
    matrix = _symmetric(rng, 5)
    garbled = [
        [matrix[i][j] if j <= i else (math.nan if (i + j) % 2 else 1e300) for j in range(5)]
        for i in range(5)
    ]

    assert symmetric_eigensystem(
        garbled, tolerance=TOLERANCE, max_iterations=ITERATIONS
    ) == symmetric_eigensystem(matrix, tolerance=TOLERANCE, max_iterations=ITERATIONS)


def test_the_eigensystem_reports_its_convergence() -> None:
    rng = random.Random(3)
    system = symmetric_eigensystem(_symmetric(rng, 8), tolerance=1e-12, max_iterations=ITERATIONS)
    report = system.convergence

    assert report.method == "the implicit QL eigensystem"
    assert report.tolerance == 1e-12 and report.limit == ITERATIONS
    assert 1 <= report.iterations < ITERATIONS
    assert 0.0 <= report.reached <= 1e-12


def test_an_eigensystem_short_of_its_criterion_raises() -> None:
    rng = random.Random(4)
    with pytest.raises(ConvergenceError) as raised:
        symmetric_eigensystem(_symmetric(rng, 8), tolerance=1e-15, max_iterations=1)

    assert raised.value.limit == 1
    assert raised.value.tolerance == 1e-15
    assert raised.value.reached > 1e-15


# --------------------------------------------------------------------------- #
# The singular value decomposition
# --------------------------------------------------------------------------- #


def _reconstruction(svd: SingularValueDecomposition, height: int, width: int) -> Matrix:
    return [
        [
            math.fsum(
                svd.singular_values[k] * svd.left_vectors[k][i] * svd.right_vectors[k][j]
                for k in range(len(svd.singular_values))
            )
            for j in range(width)
        ]
        for i in range(height)
    ]


@pytest.mark.parametrize(("height", "width"), [(6, 4), (4, 6), (5, 5), (9, 2), (2, 9), (1, 4)])
def test_a_random_matrix_is_decomposed_within_the_bound(height: int, width: int) -> None:
    rng = random.Random(height * 13 + width)
    matrix = _rectangular(rng, height, width)
    svd = singular_value_decomposition(matrix, tolerance=TOLERANCE, max_sweeps=SWEEPS)
    count = min(height, width)
    sweeps = svd.convergence.iterations
    norm = _frobenius(matrix)

    assert len(svd.singular_values) == count
    assert all(len(vector) == height for vector in svd.left_vectors)
    assert all(len(vector) == width for vector in svd.right_vectors)
    assert list(svd.singular_values) == sorted(svd.singular_values, reverse=True)
    assert min(svd.singular_values) >= 0.0
    backward = 8.0 * max(height, width) * (sweeps + 1) * EPS
    assert _orthogonality(svd.left_vectors) <= TOLERANCE + backward
    assert _orthogonality(svd.right_vectors) <= backward
    rebuilt = _reconstruction(svd, height, width)
    for i in range(height):
        for j in range(width):
            assert abs(rebuilt[i][j] - matrix[i][j]) <= backward * norm
    # The module's own singular values, computed independently (math.hypot, no vectors).
    for value, other in zip(svd.singular_values, singular_values(matrix), strict=True):
        assert abs(value - other) <= (count * TOLERANCE + 2 * backward) * norm


def test_the_two_by_two_decomposition_meets_its_closed_form() -> None:
    svd = singular_value_decomposition([[3.0, 0.0], [4.0, 5.0]], tolerance=TOLERANCE, max_sweeps=10)
    with localcontext() as context:
        context.prec = 60
        exact = (Decimal(45).sqrt(), Decimal(5).sqrt())

    for computed, value in zip(svd.singular_values, exact, strict=True):
        assert abs(Decimal(computed) - value) <= Decimal(4 * EPS) * value


def test_a_rank_one_matrix_has_one_singular_value_and_a_completed_basis() -> None:
    u = [1.0, 2.0, -1.0, 0.5]
    v = [3.0, -1.0, 2.0]
    matrix = [[a * b for b in v] for a in u]
    svd = singular_value_decomposition(matrix, tolerance=TOLERANCE, max_sweeps=SWEEPS)
    with localcontext() as context:
        context.prec = 60
        exact = (
            sum((Decimal(a) ** 2 for a in u), Decimal(0))
            * sum((Decimal(b) ** 2 for b in v), Decimal(0))
        ).sqrt()

    assert abs(Decimal(svd.singular_values[0]) - exact) <= Decimal(16 * EPS) * exact
    assert svd.singular_values[1:] == pytest.approx((0.0, 0.0), abs=16 * EPS * float(exact))
    assert _orthogonality(svd.left_vectors) <= 64 * EPS
    assert _orthogonality(svd.right_vectors) <= 64 * EPS


def test_a_zero_matrix_decomposes_into_the_standard_basis() -> None:
    svd = singular_value_decomposition(
        [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]], tolerance=TOLERANCE, max_sweeps=1
    )

    assert svd.singular_values == (0.0, 0.0)
    assert svd.left_vectors == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    assert svd.right_vectors == ((1.0, 0.0), (0.0, 1.0))
    assert svd.convergence.iterations == 0


def test_each_right_vector_has_its_largest_component_positive() -> None:
    rng = random.Random(21)
    for height, width in ((7, 4), (4, 7)):
        svd = singular_value_decomposition(
            _rectangular(rng, height, width), tolerance=TOLERANCE, max_sweeps=SWEEPS
        )
        for vector in svd.right_vectors:
            leading = max(range(len(vector)), key=lambda index: abs(vector[index]))
            assert vector[leading] > 0.0


def test_a_matrix_and_its_transpose_have_the_same_singular_values_bit_for_bit() -> None:
    rng = random.Random(8)
    matrix = _rectangular(rng, 3, 7)
    transpose = [list(column) for column in zip(*matrix, strict=True)]

    wide = singular_value_decomposition(matrix, tolerance=TOLERANCE, max_sweeps=SWEEPS)
    tall = singular_value_decomposition(transpose, tolerance=TOLERANCE, max_sweeps=SWEEPS)

    assert _hex(wide.singular_values) == _hex(tall.singular_values)


def test_a_positive_definite_matrix_has_its_eigensystem_as_its_decomposition() -> None:
    rng = random.Random(12)
    draws = _rectangular(rng, 10, 5)
    matrix = [[math.fsum(row[i] * row[j] for row in draws) for j in range(5)] for i in range(5)]
    svd = singular_value_decomposition(matrix, tolerance=TOLERANCE, max_sweeps=SWEEPS)
    system = symmetric_eigensystem(matrix, tolerance=TOLERANCE, max_iterations=ITERATIONS)

    assert svd.singular_values == pytest.approx(system.eigenvalues, rel=1e-13)
    # One sign rule for both: the vectors agree, not merely up to sign.
    for right, eigenvector in zip(svd.right_vectors, system.eigenvectors, strict=True):
        assert right == pytest.approx(eigenvector, abs=1e-12)


@pytest.mark.parametrize("power", [-700, 700])
def test_scaling_by_a_power_of_two_scales_the_singular_values_exactly(power: int) -> None:
    rng = random.Random(6)
    matrix = _rectangular(rng, 5, 3)
    scaled = [[math.ldexp(value, power) for value in row] for row in matrix]

    plain = singular_value_decomposition(matrix, tolerance=TOLERANCE, max_sweeps=SWEEPS)
    other = singular_value_decomposition(scaled, tolerance=TOLERANCE, max_sweeps=SWEEPS)

    assert other.singular_values == tuple(math.ldexp(v, power) for v in plain.singular_values)
    assert other.left_vectors == plain.left_vectors
    assert other.right_vectors == plain.right_vectors


def test_the_hilbert_matrix_condition_matches_its_published_value() -> None:
    hilbert = [[1.0 / (i + j + 1) for j in range(6)] for i in range(6)]
    values = singular_value_decomposition(
        hilbert, tolerance=TOLERANCE, max_sweeps=SWEEPS
    ).singular_values

    assert values[0] / values[-1] == pytest.approx(1.495105864e7, rel=1e-8)


def test_a_tolerance_rounding_cannot_reach_raises() -> None:
    rng = random.Random(2)
    with pytest.raises(ConvergenceError) as raised:
        singular_value_decomposition(_rectangular(rng, 6, 6), tolerance=1e-300, max_sweeps=8)

    assert raised.value.limit == 8
    assert raised.value.reached > 1e-300


def test_a_decomposition_short_of_its_criterion_raises() -> None:
    rng = random.Random(2)
    with pytest.raises(ConvergenceError):
        singular_value_decomposition(_rectangular(rng, 8, 6), tolerance=1e-15, max_sweeps=1)


# --------------------------------------------------------------------------- #
# The pseudo-inverse
# --------------------------------------------------------------------------- #


def _exact_inverse(matrix: Matrix) -> list[list[Fraction]]:
    size = len(matrix)
    augmented = [
        [Fraction(value) for value in row] + [Fraction(int(i == j)) for j in range(size)]
        for i, row in enumerate(matrix)
    ]
    for column in range(size):
        pivot = next(row for row in range(column, size) if augmented[row][column] != 0)
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        lead = augmented[column][column]
        augmented[column] = [value / lead for value in augmented[column]]
        for row in range(size):
            if row != column and augmented[row][column] != 0:
                factor = augmented[row][column]
                augmented[row] = [
                    a - factor * b for a, b in zip(augmented[row], augmented[column], strict=True)
                ]
    return [row[size:] for row in augmented]


def test_an_invertible_matrix_has_its_inverse_as_pseudo_inverse() -> None:
    matrix = [[4.0, 1.0, 0.5], [2.0, 5.0, -1.0], [0.0, 1.5, 3.0]]
    result = pseudo_inverse(matrix, relative_cutoff=1e-12, tolerance=TOLERANCE, max_sweeps=SWEEPS)
    exact = _exact_inverse(matrix)
    values = result.decomposition.singular_values
    condition = values[0] / values[-1]

    assert result.rank == 3
    for i in range(3):
        for j in range(3):
            error = abs(Fraction(result.matrix[i][j]) - exact[i][j])
            assert error <= Fraction(64 * 3 * EPS * condition) / Fraction(values[-1])


def test_a_rank_one_matrix_has_the_closed_form_pseudo_inverse() -> None:
    u = [1.0, 2.0, -1.0, 0.5]
    v = [3.0, -1.0, 2.0]
    matrix = [[a * b for b in v] for a in u]
    result = pseudo_inverse(matrix, relative_cutoff=1e-12, tolerance=TOLERANCE, max_sweeps=SWEEPS)
    scale = sum(Fraction(a) ** 2 for a in u) * sum(Fraction(b) ** 2 for b in v)

    exact = [[Fraction(v[i]) * Fraction(u[j]) / scale for j in range(4)] for i in range(3)]
    largest = max(abs(value) for row in exact for value in row)

    assert result.rank == 1
    assert len(result.matrix) == 3 and all(len(row) == 4 for row in result.matrix)
    for i in range(3):
        for j in range(4):
            assert abs(Fraction(result.matrix[i][j]) - exact[i][j]) <= Fraction(64 * EPS) * largest


@pytest.mark.parametrize("seed", range(4))
def test_the_four_moore_penrose_conditions_hold(seed: int) -> None:
    rng = random.Random(seed)
    left, right = _rectangular(rng, 6, 2), _rectangular(rng, 2, 5)
    matrix = [[_dot(row, [r[j] for r in right]) for j in range(5)] for row in left]
    result = pseudo_inverse(matrix, relative_cutoff=1e-10, tolerance=TOLERANCE, max_sweeps=SWEEPS)
    plus = [list(row) for row in result.matrix]

    def product(a: Matrix, b: Matrix) -> Matrix:
        return [[_dot(row, [r[j] for r in b]) for j in range(len(b[0]))] for row in a]

    def close(a: Matrix, b: Matrix, scale: float) -> bool:
        return all(
            abs(x - y) <= 1e-12 * scale
            for ra, rb in zip(a, b, strict=True)
            for x, y in zip(ra, rb, strict=True)
        )

    projector = product(matrix, plus)
    other = product(plus, matrix)
    assert result.rank == 2
    assert close(product(projector, matrix), matrix, _frobenius(matrix))
    assert close(product(other, plus), plus, _frobenius(plus))
    assert close(projector, [list(row) for row in zip(*projector, strict=True)], 1.0)
    assert close(other, [list(row) for row in zip(*other, strict=True)], 1.0)


def test_the_cutoff_decides_the_rank() -> None:
    matrix = [[1.0, 0.0], [0.0, 1e-10]]

    loose = pseudo_inverse(matrix, relative_cutoff=1e-8, tolerance=TOLERANCE, max_sweeps=5)
    tight = pseudo_inverse(matrix, relative_cutoff=1e-12, tolerance=TOLERANCE, max_sweeps=5)

    assert (loose.rank, loose.matrix) == (1, ((1.0, 0.0), (0.0, 0.0)))
    assert (tight.rank, tight.matrix) == (2, ((1.0, 0.0), (0.0, 1e10)))
    assert loose.threshold == 1e-8


def test_a_full_rank_pseudo_inverse_solves_least_squares() -> None:
    rng = random.Random(30)
    design = _rectangular(rng, 12, 3)
    target = [rng.uniform(-1.0, 1.0) for _ in range(12)]
    result = pseudo_inverse(design, relative_cutoff=1e-12, tolerance=TOLERANCE, max_sweeps=SWEEPS)
    coefficients = [_dot(row, target) for row in result.matrix]

    assert coefficients == pytest.approx(least_squares(design, target, 1e6).coefficients, abs=1e-12)


def test_a_zero_matrix_has_a_zero_pseudo_inverse() -> None:
    result = pseudo_inverse(
        [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]], relative_cutoff=0.0, tolerance=TOLERANCE, max_sweeps=1
    )

    assert result.rank == 0
    assert result.matrix == ((0.0, 0.0), (0.0, 0.0), (0.0, 0.0))


def test_inverting_a_subnormal_singular_value_is_refused() -> None:
    with pytest.raises(AlphaLabValidationError, match="floating-point range"):
        pseudo_inverse([[1e-310]], relative_cutoff=0.0, tolerance=TOLERANCE, max_sweeps=1)


@pytest.mark.parametrize("cutoff", [-0.1, 1.0, math.nan, math.inf, True])
def test_a_cutoff_out_of_range_is_refused(cutoff: float) -> None:
    with pytest.raises(AlphaLabValidationError, match="relative_cutoff"):
        pseudo_inverse([[1.0]], relative_cutoff=cutoff, tolerance=TOLERANCE, max_sweeps=1)


# --------------------------------------------------------------------------- #
# Arguments
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tolerance", [0.0, 1.0, -1e-9, math.nan, math.inf, True])
def test_a_tolerance_out_of_range_is_refused(tolerance: float) -> None:
    with pytest.raises(AlphaLabValidationError, match="tolerance"):
        symmetric_eigensystem([[1.0]], tolerance=tolerance, max_iterations=1)
    with pytest.raises(AlphaLabValidationError, match="tolerance"):
        singular_value_decomposition([[1.0]], tolerance=tolerance, max_sweeps=1)


@pytest.mark.parametrize("sweeps", [0, -1, True, 1.5])
def test_an_iteration_limit_out_of_range_is_refused(sweeps: int) -> None:
    with pytest.raises(AlphaLabValidationError, match="max_iterations"):
        symmetric_eigensystem([[1.0]], tolerance=TOLERANCE, max_iterations=sweeps)
    with pytest.raises(AlphaLabValidationError, match="max_sweeps"):
        singular_value_decomposition([[1.0]], tolerance=TOLERANCE, max_sweeps=sweeps)


@pytest.mark.parametrize("matrix", [[], [[]], [[1.0, 2.0], [3.0]], [[math.nan, 0.0], [0.0, 1.0]]])
def test_a_malformed_matrix_is_refused_by_every_decomposition(matrix: Matrix) -> None:
    with pytest.raises(AlphaLabValidationError):
        symmetric_eigensystem(matrix, tolerance=TOLERANCE, max_iterations=1)
    with pytest.raises(AlphaLabValidationError):
        singular_value_decomposition(matrix, tolerance=TOLERANCE, max_sweeps=1)


def test_the_arguments_are_keyword_only_and_required() -> None:
    with pytest.raises(TypeError):
        symmetric_eigensystem([[1.0]])  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        singular_value_decomposition([[1.0]], 1e-15, 10)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        pseudo_inverse([[1.0]], tolerance=TOLERANCE, max_sweeps=1)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Pinned values: the cross-host identity (ADR-0058 section 5)
# --------------------------------------------------------------------------- #

PINNED_MATRIX = [
    [4.0, 1.0, -2.0, 2.0],
    [1.0, 2.0, 0.0, 1.0],
    [-2.0, 0.0, 3.0, -2.0],
    [2.0, 1.0, -2.0, -1.0],
]
PINNED_RECTANGLE = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.5]]
PINNED_POSITIVE = [[4.0, 2.0, 0.6], [2.0, 5.0, 1.5], [0.6, 1.5, 3.0]]

#: Computed under CPython 3.12.4, 3.13.18 and 3.14.10 on macOS arm64, identical
#: in every bit; CI holds them on Linux x86-64 as well.
PINNED_EIGENVALUES: list[str] = [
    "0x1.b60e45b04578bp+2",
    "0x1.225f3cb44918ep+1",
    "0x1.1598e8d4d9bd2p+0",
    "-0x1.19483c7f40e8ap+1",
]
PINNED_EIGENVECTORS: list[list[str]] = [
    [
        "0x1.6fa3b851bb316p-1",
        "0x1.c4ebdb9c503bbp-3",
        "-0x1.1d5d28e25f511p-1",
        "0x1.69d6479dff88ap-2",
    ],
    [
        "0x1.9d1ab519d165dp-3",
        "0x1.9432c767ed5eep-1",
        "0x1.28c5cf51b82eap-1",
        "0x1.50e3449062970p-7",
    ],
    [
        "0x1.48d64db92d7dap-1",
        "-0x1.169fca25c0eafp-1",
        "0x1.0a5a84b8e451dp-1",
        "-0x1.26e02dc41145fp-3",
    ],
    [
        "-0x1.69e46686ee082p-3",
        "-0x1.6cbff06d32c7cp-3",
        "0x1.269272fe07e85p-2",
        "0x1.d93bdf6338a5ap-1",
    ],
]
PINNED_SINGULAR_VALUES: list[str] = ["0x1.3b4b1f602bcf4p+3", "0x1.a628ee283e66dp-2"]
PINNED_LEFT: list[list[str]] = [
    ["0x1.c9564e9797c7ep-3", "0x1.03d2472309f61p-1", "0x1.aa1a9793d7f21p-1"],
    ["-0x1.ef1356bf0004fp-1", "0x1.ec1a2c06f9977p-8", "0x1.04fe6293b0d65p-2"],
]
PINNED_RIGHT: list[list[str]] = [
    ["0x1.32f243acf259bp-1", "0x1.99ca453457253p-1"],
    ["0x1.99ca453457253p-1", "-0x1.32f243acf259bp-1"],
]
#: The two functions that take norms with :func:`math.hypot`, which CPython
#: computes itself rather than through the platform's libm (ADR-0058 section 3).
PINNED_DESIGN = [
    [1.0, 0.5, -1.25],
    [1.0, 1.5, 0.75],
    [1.0, -0.25, 2.0],
    [1.0, 2.5, -0.5],
    [1.0, 0.0, 1.0],
]
PINNED_TARGET = [0.3, 1.7, -0.4, 2.2, 0.9]
PINNED_LEAST_SQUARES: list[str] = [
    "0x1.398a55397a429p-3",
    "0x1.c345478a14f15p-1",
    "0x1.824c2f4ed2467p-4",
    "0x1.79a4cadaaca51p+1",
]
PINNED_HYPOT_SINGULAR_VALUES: list[str] = [
    "0x1.b3493c12694ddp+1",
    "0x1.70a395c7c69d0p+1",
    "0x1.2713359c157f1p+0",
]
PINNED_CHOLESKY: list[list[str]] = [
    ["0x1.0000000000000p+1", "0x0.0p+0", "0x0.0p+0"],
    ["0x1.0000000000000p+0", "0x1.0000000000000p+1", "0x0.0p+0"],
    ["0x1.3333333333333p-2", "0x1.3333333333333p-1", "0x1.98cc997feff4cp+0"],
]


def test_the_pinned_eigensystem_is_the_same_on_every_host() -> None:
    system = symmetric_eigensystem(PINNED_MATRIX, tolerance=TOLERANCE, max_iterations=ITERATIONS)

    assert _hex(system.eigenvalues) == PINNED_EIGENVALUES
    assert [_hex(vector) for vector in system.eigenvectors] == PINNED_EIGENVECTORS


def test_the_pinned_decomposition_is_the_same_on_every_host() -> None:
    svd = singular_value_decomposition(PINNED_RECTANGLE, tolerance=TOLERANCE, max_sweeps=SWEEPS)

    assert _hex(svd.singular_values) == PINNED_SINGULAR_VALUES
    assert [_hex(vector) for vector in svd.left_vectors] == PINNED_LEFT
    assert [_hex(vector) for vector in svd.right_vectors] == PINNED_RIGHT


def test_the_pinned_factor_is_the_same_on_every_host() -> None:
    assert [_hex(row) for row in cholesky(PINNED_POSITIVE).lower] == PINNED_CHOLESKY


def test_the_pinned_least_squares_is_the_same_on_every_host() -> None:
    """ADR-0058 section 6 puts QR least squares' coefficients in an estimate's identity."""

    solution = least_squares(PINNED_DESIGN, PINNED_TARGET, 1e6)

    assert _hex((*solution.coefficients, solution.condition)) == PINNED_LEAST_SQUARES
    assert _hex(singular_values(PINNED_DESIGN)) == PINNED_HYPOT_SINGULAR_VALUES


def test_the_result_types_are_values() -> None:
    system = symmetric_eigensystem([[1.0]], tolerance=TOLERANCE, max_iterations=1)
    svd = singular_value_decomposition([[2.0]], tolerance=TOLERANCE, max_sweeps=1)
    inverse = pseudo_inverse([[2.0]], relative_cutoff=0.0, tolerance=TOLERANCE, max_sweeps=1)

    assert isinstance(system, SymmetricEigensystem)
    assert isinstance(inverse, PseudoInverse)
    assert isinstance(cholesky([[1.0]]), CholeskyFactor)
    assert isinstance(system.convergence, ConvergenceReport)
    assert (system.eigenvalues, svd.singular_values, inverse.matrix) == ((1.0,), (2.0,), ((0.5,),))
    with pytest.raises(AttributeError):
        system.eigenvalues = (2.0,)  # type: ignore[misc]
