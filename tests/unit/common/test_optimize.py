"""Optimisation and root finding (v4.3, ADR-0058).

Each method is held to what its criterion guarantees, against an oracle that
needs no floating point where one exists:

- Brent's bracket must contain the root. The roots of polynomials with integer
  coefficients are found by Newton's method in 60-digit ``Decimal`` and
  compared with the bracket exactly.
- A quadratic's minimizer is ``A^-1 b`` in exact ``Fraction``. A gradient
  method that stops at ``||g||_inf <= tol`` is then within ``||A^-1||_inf
  tol`` of it, which is the bound held.
- A box-constrained separable quadratic's minimizer is its unconstrained one
  clamped to the box, exactly. A bound Rosenbrock problem's is known in
  closed form, ``(0.5, 0.25)``.
- A central difference's error is at most ``h^2 |f'''| / 6`` plus the rounding
  of the two values over ``2h``.

Objectives here use ``x * x``, not ``x ** 2``, so that they are class 2 and
their results can be pinned for the cross-host identity CI checks.
"""

from __future__ import annotations

import math
import random
import sys
from collections.abc import Sequence
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.exceptions import AlphaLabValidationError, ConvergenceError
from alphalab.common.optimize import (
    GOLDEN_FRACTION,
    Minimum,
    Root,
    ScalarMinimum,
    bfgs,
    brent_root,
    golden_section_minimum,
    l_bfgs_b,
    nelder_mead,
    numerical_gradient,
)

EPS = sys.float_info.epsilon


def _rosenbrock(v: Sequence[float]) -> float:
    return math.fsum(
        100.0 * (v[i + 1] - v[i] * v[i]) * (v[i + 1] - v[i] * v[i]) + (1.0 - v[i]) * (1.0 - v[i])
        for i in range(len(v) - 1)
    )


def _rosenbrock_gradient(v: Sequence[float]) -> list[float]:
    g = [0.0] * len(v)
    for i in range(len(v) - 1):
        inner = v[i + 1] - v[i] * v[i]
        g[i] += -400.0 * v[i] * inner - 2.0 * (1.0 - v[i])
        g[i + 1] += 200.0 * inner
    return g


def _polynomial_root(coefficients: list[int], low: float, high: float) -> Decimal:
    """A root of ``sum(c_k x^k)`` in ``[low, high]`` by bisection then Newton in 60 digits."""

    with localcontext() as context:
        context.prec = 60

        def value(x: Decimal) -> Decimal:
            return sum(
                (Decimal(c) * (x**k if k else Decimal(1)) for k, c in enumerate(coefficients)),
                Decimal(0),
            )

        def slope(x: Decimal) -> Decimal:
            return sum(
                (Decimal(k * c) * x ** (k - 1) for k, c in enumerate(coefficients) if k),
                Decimal(0),
            )

        a, b = Decimal(low), Decimal(high)
        for _ in range(60):
            middle = (a + b) / 2
            if (value(a) > 0) == (value(middle) > 0):
                a = middle
            else:
                b = middle
        x = (a + b) / 2
        for _ in range(8):
            x -= value(x) / slope(x)
        return x


# --------------------------------------------------------------------------- #
# Brent
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("coefficients", "low", "high"),
    [
        ([-5, -2, 0, 1], 2.0, 3.0),  # Newton's own example, x^3 - 2x - 5
        ([-2, 0, 1], 0.0, 2.0),  # sqrt(2)
        ([1, -3, 0, 1], 0.0, 1.0),  # a cubic with three real roots, the middle one
        ([-1, 0, 0, 0, 0, 1, 1], 0.0, 2.0),  # x^6 + x^5 - 1
    ],
)
@pytest.mark.parametrize("tolerance", [1e-4, 1e-10, 1e-15])
def test_brents_bracket_holds_the_root_within_its_tolerance(
    coefficients: list[int], low: float, high: float, tolerance: float
) -> None:
    def polynomial(x: float) -> float:
        total = 0.0
        for c in reversed(coefficients):
            total = total * x + c
        return total

    result = brent_root(polynomial, low, high, tolerance=tolerance, max_iterations=200)
    root = _polynomial_root(coefficients, low, high)
    left, right = result.bracket

    assert result.x in result.bracket
    assert result.convergence.reached <= tolerance
    if result.value == 0.0:
        # An exact zero of the polynomial as evaluated: Horner's rounding is at most
        # 2n eps sum |c_k x^k| (Higham, section 5.1), over the slope there.
        degree = len(coefficients) - 1
        x = Decimal(result.x)
        magnitude = sum(
            (abs(Decimal(c)) * abs(x) ** k for k, c in enumerate(coefficients)), Decimal(0)
        )
        slope = sum(
            (Decimal(k * c) * x ** (k - 1) for k, c in enumerate(coefficients) if k), Decimal(0)
        )
        assert abs(x - root) <= Decimal(2 * degree) * Decimal(EPS) * magnitude / abs(slope) * 2
    else:
        assert Decimal(left) <= root <= Decimal(right)
        assert (right - left) - 4 * EPS * abs(result.x) <= tolerance
        assert abs(Decimal(result.x) - root) <= Decimal(tolerance + 4 * EPS * abs(result.x))


def test_brent_returns_an_exact_zero_as_it_finds_one() -> None:
    result = brent_root(lambda x: x - 0.5, 0.0, 1.0, tolerance=1e-12, max_iterations=50)

    assert (result.x, result.value, result.convergence.reached) == (0.5, 0.0, 0.0)


def test_brent_accepts_a_root_at_an_end() -> None:
    result = brent_root(lambda x: x * (x - 1.0), 1.0, 3.0, tolerance=1e-9, max_iterations=5)

    assert (result.x, result.evaluations, result.convergence.iterations) == (1.0, 2, 0)


def test_brent_refuses_ends_of_one_sign() -> None:
    with pytest.raises(AlphaLabValidationError, match="does not bracket"):
        brent_root(lambda x: x * x + 1.0, -1.0, 1.0, tolerance=1e-9, max_iterations=50)


def test_brent_short_of_its_tolerance_raises() -> None:
    with pytest.raises(ConvergenceError) as raised:
        brent_root(lambda x: x * x * x - 2.0 * x - 5.0, 2.0, 3.0, tolerance=1e-15, max_iterations=2)

    assert raised.value.limit == 2 and raised.value.reached > 1e-15


# --------------------------------------------------------------------------- #
# Golden section
# --------------------------------------------------------------------------- #


def test_the_golden_fraction_is_the_reciprocal_of_phi() -> None:
    assert abs(GOLDEN_FRACTION * (1.0 + GOLDEN_FRACTION) - 1.0) <= EPS


@pytest.mark.parametrize(("centre", "low", "high"), [(2.0, 0.0, 5.0), (-0.3, -1.0, 1.0)])
def test_golden_section_brackets_a_parabolas_minimum(
    centre: float, low: float, high: float
) -> None:
    result = golden_section_minimum(
        lambda x: (x - centre) * (x - centre) + 1.0, low, high, tolerance=1e-7, max_iterations=200
    )

    assert result.bracket[0] <= centre <= result.bracket[1]
    assert abs(result.x - centre) <= 1e-7 + 4 * EPS * max(abs(low), abs(high))
    assert result.evaluations == result.convergence.iterations + 2


def test_golden_section_converges_to_an_end_minimum() -> None:
    result = golden_section_minimum(lambda x: x, 0.0, 1.0, tolerance=1e-9, max_iterations=200)

    assert result.bracket[0] == 0.0 and result.x <= 1e-9


def test_golden_section_short_of_its_tolerance_raises() -> None:
    with pytest.raises(ConvergenceError):
        golden_section_minimum(lambda x: x * x, -1.0, 1.0, tolerance=1e-12, max_iterations=10)


# --------------------------------------------------------------------------- #
# Nelder-Mead
# --------------------------------------------------------------------------- #


def test_nelder_mead_finds_the_rosenbrock_minimum() -> None:
    result = nelder_mead(_rosenbrock, [-1.2, 1.0], step=0.5, tolerance=1e-10, max_iterations=2000)

    assert result.x == pytest.approx((1.0, 1.0), abs=1e-8)
    assert result.gradient is None and result.gradient_evaluations == 0
    assert result.convergence.reached <= 1e-10


def test_nelder_mead_finds_a_quadratic_bowls_centre() -> None:
    centre = (0.5, -2.0, 3.0)
    weights = (1.0, 4.0, 0.25)

    def bowl(v: Sequence[float]) -> float:
        return math.fsum(w * (a - c) * (a - c) for w, a, c in zip(weights, v, centre, strict=True))

    result = nelder_mead(
        bowl, [0.0, 0.0, 0.0], step=(1.0, 1.0, 2.0), tolerance=1e-9, max_iterations=5000
    )

    assert result.x == pytest.approx(centre, abs=1e-7)


def test_nelder_mead_on_a_constant_keeps_its_start() -> None:
    """Every comparison ties; ties rank the newer vertex last, so the start stays best."""

    result = nelder_mead(lambda v: 7.0, [1.0, 2.0], step=1.0, tolerance=1e-6, max_iterations=200)

    assert result.x == (1.0, 2.0)
    assert result.value == 7.0


def test_nelder_mead_refuses_a_step_of_the_wrong_length() -> None:
    with pytest.raises(AlphaLabValidationError, match="step has"):
        nelder_mead(_rosenbrock, [0.0, 0.0], step=(1.0,), tolerance=1e-6, max_iterations=10)


def test_nelder_mead_short_of_its_tolerance_raises() -> None:
    with pytest.raises(ConvergenceError):
        nelder_mead(_rosenbrock, [-1.2, 1.0], step=0.5, tolerance=1e-10, max_iterations=20)


# --------------------------------------------------------------------------- #
# Gradients
# --------------------------------------------------------------------------- #


def test_a_central_difference_is_within_its_truncation_and_rounding() -> None:
    # f = x^3 y + y^2: f_x = 3 x^2 y, f_y = x^3 + 2y; |f_xxx| = 6|y|, |f_yyy| = 0.
    def cubic(v: Sequence[float]) -> float:
        x, y = v
        return x * x * x * y + y * y

    point = (1.5, -0.75)
    step = 1e-5
    computed = numerical_gradient(cubic, point, step=step)
    exact = (3 * 1.5 * 1.5 * -0.75, 1.5**3 + 2 * -0.75)
    for component, value, coordinate, third in zip(
        computed, exact, point, (6 * 0.75, 0.0), strict=True
    ):
        h = step * max(1.0, abs(coordinate))
        rounding = 4 * EPS * abs(cubic(point)) / h + 4 * EPS * abs(value)
        assert abs(component - value) <= h * h * third / 6 + rounding


def test_a_step_below_the_spacing_of_the_numbers_is_refused() -> None:
    with pytest.raises(AlphaLabValidationError, match="spacing"):
        numerical_gradient(_rosenbrock, [1e20, 1.0], step=1e-30)


# --------------------------------------------------------------------------- #
# BFGS
# --------------------------------------------------------------------------- #


def _quadratic(size: int, seed: int) -> tuple[list[list[float]], list[float]]:
    rng = random.Random(seed)
    draws = [[float(rng.randint(-3, 3)) for _ in range(size)] for _ in range(size + 2)]
    matrix = [
        [sum(row[i] * row[j] for row in draws) + (1.0 if i == j else 0.0) for j in range(size)]
        for i in range(size)
    ]
    return matrix, [float(rng.randint(-5, 5)) for _ in range(size)]


def _exact_solution(matrix: list[list[float]], rhs: list[float]) -> list[Fraction]:
    size = len(matrix)
    rows = [[Fraction(v) for v in matrix[i]] + [Fraction(rhs[i])] for i in range(size)]
    for column in range(size):
        pivot = next(r for r in range(column, size) if rows[r][column] != 0)
        rows[column], rows[pivot] = rows[pivot], rows[column]
        rows[column] = [v / rows[column][column] for v in rows[column]]
        for r in range(size):
            if r != column:
                factor = rows[r][column]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[column], strict=True)]
    return [row[size] for row in rows]


def _inverse_norm(matrix: list[list[float]]) -> float:
    """``||A^-1||_inf``, exactly."""

    size = len(matrix)
    columns = [
        _exact_solution(matrix, [1.0 if i == j else 0.0 for i in range(size)]) for j in range(size)
    ]
    return float(max(sum(abs(columns[j][i]) for j in range(size)) for i in range(size)))


def _bowl(size: int, seed: int) -> tuple[list[list[float]], list[float]]:
    """``A``, symmetric positive definite with small integers, and a minimizer exact in binary."""

    matrix, _ = _quadratic(size, seed)
    rng = random.Random(seed + 100)
    return matrix, [rng.randint(-40, 40) / 8.0 for _ in range(size)]


@pytest.mark.parametrize(("size", "seed"), [(2, 1), (5, 2), (9, 3)])
def test_bfgs_meets_a_quadratics_exact_minimizer_within_its_gradient_bound(
    size: int, seed: int
) -> None:
    """``f = (x - m)' A (x - m) / 2``: where ``||g|| <= tol``, ``||x - m|| <= ||A^-1|| tol``.

    Both norms are the infinity norm; ``m`` is exact in binary, so the oracle is too.
    """

    matrix, minimizer = _bowl(size, seed)

    def gradient(v: Sequence[float]) -> list[float]:
        return [
            math.fsum(matrix[i][j] * (v[j] - minimizer[j]) for j in range(size))
            for i in range(size)
        ]

    def objective(v: Sequence[float]) -> float:
        return 0.5 * math.fsum(
            (a - m) * g for a, m, g in zip(v, minimizer, gradient(v), strict=True)
        )

    tolerance = 1e-10
    result = bfgs(objective, gradient, [0.0] * size, tolerance=tolerance, max_iterations=200)
    bound = _inverse_norm(matrix) * tolerance * (1 + 1e-6)

    assert result.convergence.reached <= tolerance
    for computed, value in zip(result.x, minimizer, strict=True):
        assert abs(computed - value) <= bound


def test_a_gradient_tolerance_below_the_objectives_rounding_raises_with_what_was_reached() -> None:
    """``f = x'Ax/2 - b'x`` has a minimum value of order 10, so its rounding is 1e-15.

    Cutting the gradient to 1e-10 would lower ``f`` by about 1e-20, which no
    line search can see. The method says so, with the gradient it reached,
    rather than return a point it could not show met the criterion.
    """

    matrix, rhs = _quadratic(5, 2)

    def objective(v: Sequence[float]) -> float:
        return math.fsum(
            [
                *(0.5 * v[i] * matrix[i][j] * v[j] for i in range(5) for j in range(5)),
                *(-b * x for b, x in zip(rhs, v, strict=True)),
            ]
        )

    def gradient(v: Sequence[float]) -> list[float]:
        return [math.fsum([*(matrix[i][j] * v[j] for j in range(5)), -rhs[i]]) for i in range(5)]

    with pytest.raises(ConvergenceError, match="line search") as raised:
        bfgs(objective, gradient, [0.0] * 5, tolerance=1e-10, max_iterations=200)
    assert 1e-10 < raised.value.reached < 1e-6
    # A tolerance the rounding supports is met.
    result = bfgs(objective, gradient, [0.0] * 5, tolerance=1e-6, max_iterations=200)
    exact = _exact_solution(matrix, rhs)
    bound = _inverse_norm(matrix) * 1e-6 * (1 + 1e-6)
    for computed, value in zip(result.x, exact, strict=True):
        assert abs(Fraction(computed) - value) <= Fraction(bound)


@pytest.mark.parametrize("size", [2, 5, 10])
def test_bfgs_finds_the_rosenbrock_minimum(size: int) -> None:
    start = [-1.2 if i % 2 == 0 else 1.0 for i in range(size)]
    result = bfgs(_rosenbrock, _rosenbrock_gradient, start, tolerance=1e-9, max_iterations=1000)

    assert result.x == pytest.approx([1.0] * size, abs=1e-7)
    assert result.gradient is not None and max(abs(g) for g in result.gradient) <= 1e-9


def test_bfgs_with_a_difference_gradient() -> None:
    result = bfgs(
        _rosenbrock,
        lambda v: numerical_gradient(_rosenbrock, v, step=6e-6),
        [-1.2, 1.0],
        tolerance=1e-6,
        max_iterations=500,
    )

    assert result.x == pytest.approx((1.0, 1.0), abs=1e-5)


def test_a_gradient_of_the_wrong_length_is_refused() -> None:
    with pytest.raises(AlphaLabValidationError, match="components"):
        bfgs(_rosenbrock, lambda v: [0.0], [1.0, 2.0], tolerance=1e-6, max_iterations=5)


# --------------------------------------------------------------------------- #
# L-BFGS-B
# --------------------------------------------------------------------------- #


def test_l_bfgs_b_clamps_a_separable_quadratic_exactly() -> None:
    centre = [3.0, -2.0, 0.5, 7.0, -9.0]
    bounds = [(0.0, 1.0), (-1.0, 1.0), (None, None), (None, 5.0), (-3.0, None)]
    result = l_bfgs_b(
        lambda v: math.fsum((a - c) * (a - c) for a, c in zip(v, centre, strict=True)),
        lambda v: [2.0 * (a - c) for a, c in zip(v, centre, strict=True)],
        [0.5, 0.0, 0.0, 0.0, 0.0],
        bounds=bounds,
        memory=5,
        tolerance=1e-12,
        max_iterations=100,
    )

    assert result.x == (1.0, -1.0, 0.5, 5.0, -3.0)


def test_l_bfgs_b_meets_a_bound_rosenbrock_minimum() -> None:
    """With x <= 0.5 the minimum is at (0.5, 0.25), where df/dx = -1 holds x at its bound."""

    result = l_bfgs_b(
        _rosenbrock,
        _rosenbrock_gradient,
        [-1.2, 1.0],
        bounds=[(-2.0, 0.5), (-2.0, 2.0)],
        memory=10,
        tolerance=1e-10,
        max_iterations=500,
    )

    assert result.x[0] == 0.5
    assert result.x[1] == pytest.approx(0.25, abs=1e-12)
    assert result.gradient is not None and result.gradient[0] == pytest.approx(-1.0, abs=1e-10)


def test_l_bfgs_b_without_bounds_is_a_quasi_newton_method() -> None:
    result = l_bfgs_b(
        _rosenbrock,
        _rosenbrock_gradient,
        [-1.2, 1.0, -1.2, 1.0],
        bounds=[(None, None)] * 4,
        memory=7,
        tolerance=1e-9,
        max_iterations=1000,
    )

    assert result.x == pytest.approx([1.0] * 4, abs=1e-7)


def test_l_bfgs_b_solves_its_authors_first_driver_problem() -> None:
    """Byrd, Lu, Nocedal and Zhu's ``driver1``: 25 variables, every other one bounded below by 1."""

    size = 25

    def objective(x: Sequence[float]) -> float:
        return 0.25 * (x[0] - 1.0) * (x[0] - 1.0) + math.fsum(
            4.0 * (x[i + 1] - x[i] * x[i]) * (x[i + 1] - x[i] * x[i]) for i in range(size - 1)
        )

    def gradient(x: Sequence[float]) -> list[float]:
        g = [0.0] * size
        t1 = x[1] - x[0] * x[0]
        g[0] = 2.0 * (x[0] - 1.0) - 16.0 * x[0] * t1
        for i in range(1, size - 1):
            t2, t1 = t1, x[i + 1] - x[i] * x[i]
            g[i] = 8.0 * t2 - 16.0 * x[i] * t1
        g[size - 1] = 8.0 * t1
        return g

    bounds = [(1.0 if i % 2 == 0 else -100.0, 100.0) for i in range(size)]
    result = l_bfgs_b(
        objective,
        gradient,
        [3.0] * size,
        bounds=bounds,
        memory=5,
        tolerance=1e-8,
        max_iterations=500,
    )

    assert result.value < 1e-12
    assert all(low <= x <= high for x, (low, high) in zip(result.x, bounds, strict=True))


def test_l_bfgs_b_projects_its_start_onto_the_bounds() -> None:
    result = l_bfgs_b(
        lambda v: v[0] * v[0],
        lambda v: [2.0 * v[0]],
        [-5.0],
        bounds=[(1.0, 2.0)],
        memory=3,
        tolerance=1e-12,
        max_iterations=10,
    )

    assert result.x == (1.0,) and result.convergence.iterations == 0


@pytest.mark.parametrize(
    "bounds",
    [
        [(2.0, 1.0)],
        [(math.nan, 1.0)],
        [(math.inf, None)],
        [(None, -math.inf)],
        [(0.0, 1.0), (0.0, 1.0)],
    ],
)
def test_l_bfgs_b_refuses_malformed_bounds(bounds: list[tuple[float | None, float | None]]) -> None:
    with pytest.raises(AlphaLabValidationError, match="bounds"):
        l_bfgs_b(
            lambda v: v[0],
            lambda v: [1.0],
            [0.5],
            bounds=bounds,
            memory=3,
            tolerance=1e-9,
            max_iterations=10,
        )


def test_l_bfgs_b_short_of_its_tolerance_raises() -> None:
    with pytest.raises(ConvergenceError):
        l_bfgs_b(
            _rosenbrock,
            _rosenbrock_gradient,
            [-1.2, 1.0],
            bounds=[(None, None)] * 2,
            memory=5,
            tolerance=1e-10,
            max_iterations=3,
        )


# --------------------------------------------------------------------------- #
# Arguments
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tolerance", [0.0, -1.0, math.nan, math.inf, True])
def test_every_method_refuses_a_tolerance_out_of_range(tolerance: float) -> None:
    with pytest.raises(AlphaLabValidationError):
        brent_root(lambda x: x, -1.0, 1.0, tolerance=tolerance, max_iterations=5)
    with pytest.raises(AlphaLabValidationError):
        golden_section_minimum(lambda x: x, -1.0, 1.0, tolerance=tolerance, max_iterations=5)
    with pytest.raises(AlphaLabValidationError):
        nelder_mead(_rosenbrock, [0.0, 0.0], step=1.0, tolerance=tolerance, max_iterations=5)
    with pytest.raises(AlphaLabValidationError):
        bfgs(_rosenbrock, _rosenbrock_gradient, [0.0, 0.0], tolerance=tolerance, max_iterations=5)


@pytest.mark.parametrize("limit", [0, -3, True, 2.0])
def test_every_method_refuses_a_limit_out_of_range(limit: int) -> None:
    with pytest.raises(AlphaLabValidationError):
        brent_root(lambda x: x, -1.0, 1.0, tolerance=1e-9, max_iterations=limit)
    with pytest.raises(AlphaLabValidationError):
        l_bfgs_b(
            _rosenbrock,
            _rosenbrock_gradient,
            [0.0, 0.0],
            bounds=[(None, None)] * 2,
            memory=limit,
            tolerance=1e-9,
            max_iterations=5,
        )


def test_a_function_returning_a_non_number_is_refused_with_its_point() -> None:
    with pytest.raises(AlphaLabValidationError, match="returned nan"):
        brent_root(lambda x: math.nan, -1.0, 1.0, tolerance=1e-9, max_iterations=5)
    with pytest.raises(AlphaLabValidationError, match="returned inf"):
        nelder_mead(lambda v: math.inf, [0.0], step=1.0, tolerance=1e-9, max_iterations=5)


def test_the_tolerances_and_limits_are_required() -> None:
    with pytest.raises(TypeError):
        brent_root(lambda x: x, -1.0, 1.0)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        bfgs(_rosenbrock, _rosenbrock_gradient, [0.0, 0.0])  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        numerical_gradient(_rosenbrock, [0.0, 0.0])  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Pinned values: the cross-host identity (ADR-0058 section 5)
# --------------------------------------------------------------------------- #

#: Computed under CPython 3.12.4, 3.13.18 and 3.14.10 on macOS arm64, identical
#: in every bit; CI holds them on Linux x86-64 as well.
PINNED: dict[str, list[str]] = {
    "bfgs": ["0x1.0000000000021p+0", "0x1.0000000000040p+0"],
    "brent": ["0x1.0c1a4350819e4p+1", "0x1.0000000000000p-48"],
    "golden": ["0x1.fffffffe4ebb8p+0", "0x1.6ea465aa20000p-63"],
    "l_bfgs_b": ["0x1.0000000000000p-1", "0x1.079a9d260511cp-2", "0x1.0f6f0c4590a05p-4"],
    "nelder_mead": ["0x1.ffffffffd8b4ep-1", "0x1.ffffffffae93bp-1"],
}


def _pinned_results() -> dict[str, list[str]]:
    root = brent_root(
        lambda x: x * x * x - 2.0 * x - 5.0, 2.0, 3.0, tolerance=1e-15, max_iterations=100
    )
    golden = golden_section_minimum(
        lambda x: (x - 2.0) * (x - 2.0), 0.0, 5.0, tolerance=1e-8, max_iterations=200
    )
    simplex = nelder_mead(_rosenbrock, [-1.2, 1.0], step=0.5, tolerance=1e-10, max_iterations=2000)
    quasi = bfgs(
        _rosenbrock, _rosenbrock_gradient, [-1.2, 1.0], tolerance=1e-10, max_iterations=500
    )
    bounded = l_bfgs_b(
        _rosenbrock,
        _rosenbrock_gradient,
        [-1.2, 1.0, -1.2],
        bounds=[(-2.0, 0.5), (None, None), (-2.0, 2.0)],
        memory=5,
        tolerance=1e-10,
        max_iterations=500,
    )
    return {
        "brent": [root.x.hex(), root.value.hex()],
        "golden": [golden.x.hex(), golden.value.hex()],
        "nelder_mead": [value.hex() for value in simplex.x],
        "bfgs": [value.hex() for value in quasi.x],
        "l_bfgs_b": [value.hex() for value in bounded.x],
    }


def test_the_pinned_results_are_the_same_on_every_host() -> None:
    assert _pinned_results() == PINNED


def test_the_result_types_carry_their_reports() -> None:
    root = brent_root(lambda x: x - 1.0, 0.0, 3.0, tolerance=1e-9, max_iterations=50)
    minimum = golden_section_minimum(lambda x: x * x, -1.0, 1.0, tolerance=1e-6, max_iterations=100)
    simplex = nelder_mead(
        lambda v: v[0] * v[0], [1.0], step=1.0, tolerance=1e-6, max_iterations=200
    )

    assert isinstance(root, Root) and isinstance(minimum, ScalarMinimum)
    assert isinstance(simplex, Minimum)
    for result in (root, minimum, simplex):
        assert isinstance(result.convergence, ConvergenceReport)
    with pytest.raises(AttributeError):
        root.x = 2.0  # type: ignore[misc]
