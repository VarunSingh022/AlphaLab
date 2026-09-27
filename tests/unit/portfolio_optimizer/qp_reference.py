"""An exact, independent reference for strictly convex quadratic programs.

Enumerates every candidate active set, solves each KKT system in rational
arithmetic, and returns the one point that is primal feasible with
non-negative inequality multipliers -- the unique optimum -- or ``None`` when
no candidate is, which proves infeasibility. Shares no code with
:mod:`alphalab.portfolio_optimizer.quadratic`; exponential in the number of
inequalities, so it is for small programs only.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from fractions import Fraction

type Row = tuple[list[float], float, bool]


def solve_exact(matrix: list[list[Fraction]], rhs: list[Fraction]) -> list[Fraction] | None:
    size = len(matrix)
    work = [[*row, value] for row, value in zip(matrix, rhs, strict=True)]
    for column in range(size):
        pivot = next((row for row in range(column, size) if work[row][column] != 0), None)
        if pivot is None:
            return None
        work[column], work[pivot] = work[pivot], work[column]
        for row in range(size):
            if row != column and work[row][column] != 0:
                factor = work[row][column] / work[column][column]
                work[row] = [a - factor * b for a, b in zip(work[row], work[column], strict=True)]
    return [work[index][size] / work[index][index] for index in range(size)]


def exact_optimum(
    hessian: Sequence[Sequence[float]], linear: Sequence[float], rows: Sequence[Row]
) -> list[float] | None:
    size = len(linear)
    equalities = [row for row in rows if row[2]]
    inequalities = [row for row in rows if not row[2]]
    for count in range(len(inequalities) + 1):
        for chosen in itertools.combinations(range(len(inequalities)), count):
            active = equalities + [inequalities[index] for index in chosen]
            if len(active) > size:
                continue
            order = size + len(active)
            matrix = [[Fraction(0)] * order for _ in range(order)]
            rhs = [Fraction(0)] * order
            for i in range(size):
                for j in range(size):
                    matrix[i][j] = Fraction(hessian[i][j])
                for k, (normal, _, _) in enumerate(active):
                    matrix[i][size + k] = -Fraction(normal[i])
                rhs[i] = -Fraction(linear[i])
            for k, (normal, bound, _) in enumerate(active):
                for i in range(size):
                    matrix[size + k][i] = Fraction(normal[i])
                rhs[size + k] = Fraction(bound)
            answer = solve_exact(matrix, rhs)
            if answer is None:
                continue
            x, duals = answer[:size], answer[size:]
            if any(duals[len(equalities) + index] < 0 for index in range(count)):
                continue
            feasible = all(
                (sum(Fraction(n[i]) * x[i] for i in range(size)) - Fraction(b)) >= 0
                for n, b, equal in rows
                if not equal
            ) and all(
                sum(Fraction(n[i]) * x[i] for i in range(size)) == Fraction(b)
                for n, b, equal in rows
                if equal
            )
            if feasible:
                return [float(value) for value in x]
    return None
