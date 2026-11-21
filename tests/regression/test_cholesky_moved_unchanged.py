"""The optimizer's Cholesky moved to ``common.linalg`` unchanged (v4.3, ADR-0058).

From v3.8 to v4.2 the constrained optimizer factored its Hessian with a private
``quadratic._cholesky``, and Black-Litterman solved its view system with that
factor through ``quadratic._cholesky_solve``. v4.3 makes
:func:`alphalab.common.linalg.cholesky` the library's one factorization of a
positive definite matrix, and both now call it.

A portfolio is built on that factor, so the move is held to the last bit. The
two functions are copied below verbatim as they stood at v4.2.1. The shared
factorization must agree with them float for float -- every entry of every
factor, every pivot ratio, every solve -- and refuse exactly where they returned
nothing. The matrices are of the kinds the two callers hand it: well and badly
conditioned covariances, Hessians near the pivot floor, and a view system
whose upper triangle differs from its lower in the last bits, which neither
version reads.
"""

from __future__ import annotations

import math
import random
import sys
from collections.abc import Sequence

import pytest

from alphalab.common.linalg import NotPositiveDefiniteError, cholesky

_EPSILON = sys.float_info.epsilon


# --------------------------------------------------------------------------- #
# v4.2.1's alphalab/portfolio_optimizer/quadratic.py, verbatim
# --------------------------------------------------------------------------- #


def _v421_cholesky(
    hessian: Sequence[Sequence[float]],
) -> tuple[list[list[float]], float] | None:
    """Lower-triangular ``L`` with ``G = L L'``, and the pivot ratio; ``None`` if not PD."""

    size = len(hessian)
    largest = max(hessian[index][index] for index in range(size))
    floor = size * _EPSILON * largest
    lower = [[0.0] * size for _ in range(size)]
    smallest_pivot = math.inf
    largest_pivot = 0.0
    for row in range(size):
        for column in range(row + 1):
            total = hessian[row][column] - math.fsum(
                lower[row][k] * lower[column][k] for k in range(column)
            )
            if row == column:
                if total <= floor:
                    return None
                smallest_pivot = min(smallest_pivot, total)
                largest_pivot = max(largest_pivot, total)
                lower[row][row] = math.sqrt(total)
            else:
                lower[row][column] = total / lower[column][column]
    return lower, smallest_pivot / largest_pivot


def _v421_cholesky_solve(lower: Sequence[Sequence[float]], rhs: Sequence[float]) -> list[float]:
    size = len(lower)
    forward = [0.0] * size
    for row in range(size):
        forward[row] = (
            rhs[row] - math.fsum(lower[row][k] * forward[k] for k in range(row))
        ) / lower[row][row]
    solution = [0.0] * size
    for row in range(size - 1, -1, -1):
        solution[row] = (
            forward[row] - math.fsum(lower[k][row] * solution[k] for k in range(row + 1, size))
        ) / lower[row][row]
    return solution


# --------------------------------------------------------------------------- #
# The matrices
# --------------------------------------------------------------------------- #


def _gram(rng: random.Random, size: int, rows: int, ridge: float) -> list[list[float]]:
    """``B'B / rows + ridge I``: a covariance of ``rows`` draws, conditioned by ``ridge``."""

    draws = [[rng.gauss(0.0, 1.0) for _ in range(size)] for _ in range(rows)]
    return [
        [
            math.fsum(draw[i] * draw[j] for draw in draws) / rows + (ridge if i == j else 0.0)
            for j in range(size)
        ]
        for i in range(size)
    ]


def _cases() -> list[tuple[str, list[list[float]]]]:
    rng = random.Random(20261114)
    cases: list[tuple[str, list[list[float]]]] = []
    for size in (1, 2, 3, 5, 8, 13):
        cases.append((f"covariance {size}", _gram(rng, size, size + 20, 0.0)))
        cases.append((f"ridged {size}", _gram(rng, size, 2 * size + 3, 0.5)))
        cases.append((f"short sample {size}", _gram(rng, size, size + 1, 1e-9)))
    for size in (2, 4, 6):
        # Near the floor: a rank-deficient Gram matrix plus a ridge a few eps wide.
        base = _gram(rng, size, size - 1, 0.0)
        largest = max(base[i][i] for i in range(size))
        for multiple in (0.0, 0.01, 0.5, 2.0, 8.0, 64.0):
            ridge = multiple * size * _EPSILON * largest
            cases.append(
                (
                    f"floor {size} x{multiple}",
                    [
                        [base[i][j] + (ridge if i == j else 0.0) for j in range(size)]
                        for i in range(size)
                    ],
                )
            )
    for size in (3, 5):
        # Black-Litterman's system is symmetric only to rounding; the upper triangle is not read.
        system = _gram(rng, size, size + 4, 0.01)
        for i in range(size):
            for j in range(i + 1, size):
                system[i][j] = math.nextafter(system[i][j], math.inf)
        cases.append((f"rounded upper {size}", system))
    # Indefinite and semidefinite: refused by both.
    cases.append(("indefinite", [[1.0, 2.0], [2.0, 1.0]]))
    cases.append(("semidefinite", [[1.0, 1.0], [1.0, 1.0]]))
    cases.append(("negative diagonal", [[-1.0, 0.0], [0.0, 1.0]]))
    cases.append(("zero", [[0.0, 0.0], [0.0, 0.0]]))
    return cases


CASES = _cases()


@pytest.mark.parametrize(("name", "matrix"), CASES, ids=[name for name, _ in CASES])
def test_the_shared_factor_is_v421s_float_for_float(name: str, matrix: list[list[float]]) -> None:
    reference = _v421_cholesky(matrix)

    if reference is None:
        with pytest.raises(NotPositiveDefiniteError):
            cholesky(matrix)
        return
    factor = cholesky(matrix)
    lower, ratio = reference
    assert [[value.hex() for value in row] for row in factor.lower] == [
        [value.hex() for value in row] for row in lower
    ]
    assert factor.pivot_ratio.hex() == ratio.hex()


@pytest.mark.parametrize(("name", "matrix"), CASES, ids=[name for name, _ in CASES])
def test_the_shared_solve_is_v421s_float_for_float(name: str, matrix: list[list[float]]) -> None:
    reference = _v421_cholesky(matrix)
    if reference is None:
        return
    factor = cholesky(matrix)
    rng = random.Random(len(matrix) * 7919)
    for _ in range(5):
        rhs = [rng.uniform(-1.0, 1.0) * 10.0 ** rng.randint(-3, 3) for _ in matrix]
        expected = _v421_cholesky_solve(reference[0], rhs)
        assert [value.hex() for value in factor.solve(rhs)] == [value.hex() for value in expected]


def test_both_callers_call_the_shared_factorization() -> None:
    """The optimizer's name delegates; Black-Litterman calls it directly; neither factors."""

    import importlib
    import inspect

    quadratic = importlib.import_module("alphalab.portfolio_optimizer.quadratic")
    black_litterman = importlib.import_module("alphalab.portfolio_optimizer.black_litterman")

    assert quadratic.cholesky is cholesky
    assert black_litterman.cholesky is cholesky
    assert "math.sqrt" not in inspect.getsource(quadratic._cholesky)
    assert not hasattr(quadratic, "_cholesky_solve")
    assert "math.sqrt(total)" not in inspect.getsource(black_litterman)
