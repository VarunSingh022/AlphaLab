"""An exact, independent reference for bounded linear programs.

Enumerates every candidate vertex -- each choice of as many active constraints as
there are variables, every equality among them -- solves it in rational
arithmetic, and keeps the feasible one with the least objective. A bounded
polyhedron that is not empty has a vertex, so no feasible candidate proves the
program infeasible. Shares no code with :mod:`alphalab.portfolio_optimizer.linear`;
exponential in the number of constraints, so it is for small programs only.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from fractions import Fraction

from tests.unit.portfolio_optimizer.qp_reference import solve_exact

#: ``(normal, bound, equality)``: ``normal . x = bound`` when ``equality``, else ``>=``.
type Row = tuple[Sequence[float], float, bool]


def _dot(normal: Sequence[Fraction], x: Sequence[Fraction]) -> Fraction:
    return sum((a * b for a, b in zip(normal, x, strict=True)), Fraction(0))


def exact_vertex_optimum(
    costs: Sequence[float],
    rows: Sequence[Row],
    lower: Sequence[float],
    upper: Sequence[float],
) -> tuple[Fraction, list[Fraction]] | None:
    """The least objective over the program's vertices and a vertex reaching it, or ``None``."""

    size = len(costs)
    constraints: list[tuple[list[Fraction], Fraction, bool]] = [
        ([Fraction(value) for value in normal], Fraction(bound), equality)
        for normal, bound, equality in rows
    ]
    for j in range(size):
        unit = [Fraction(0)] * size
        unit[j] = Fraction(1)
        constraints.append((unit, Fraction(lower[j]), False))
        negative = [Fraction(0)] * size
        negative[j] = Fraction(-1)
        constraints.append((negative, -Fraction(upper[j]), False))
    equalities = [constraint for constraint in constraints if constraint[2]]
    inequalities = [constraint for constraint in constraints if not constraint[2]]
    needed = size - len(equalities)
    if needed < 0:
        raise ValueError("the reference takes no more equalities than variables")
    cost = [Fraction(value) for value in costs]
    best: tuple[Fraction, list[Fraction]] | None = None
    for chosen in itertools.combinations(range(len(inequalities)), needed):
        active = equalities + [inequalities[index] for index in chosen]
        x = solve_exact([normal for normal, _, _ in active], [bound for _, bound, _ in active])
        if x is None:
            continue
        feasible = all(
            _dot(normal, x) == bound if equality else _dot(normal, x) >= bound
            for normal, bound, equality in constraints
        )
        if feasible:
            value = _dot(cost, x)
            if best is None or value < best[0]:
                best = (value, x)
    return best
