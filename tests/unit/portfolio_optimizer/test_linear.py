"""The certified linear program, against references that share no code with it (ADR-0063).

* **Generated programs** -- two to four variables, up to one equality and three
  inequalities, integer data, some variables fixed -- are held to the exhaustive
  exact vertex enumeration in :mod:`tests.unit.portfolio_optimizer.lp_reference`:
  an optimum to the reference's exact value, an infeasible program wherever the
  reference finds no feasible vertex, its Farkas certificate verified in exact
  arithmetic. The seeds reach both outcomes and both certificates.
* **Hand cases.** A degenerate vertex is certified at the vertex, exactly, and
  so is an optimal edge, at one of its vertices; dependent rows, which have no
  basis, at the interior; an infeasible row by a Farkas certificate that names
  it; shifted and fixed bounds; the tolerance in each row's own units. An
  infeasible program is proven once its residual stalls, and a feasible one that
  stalls resumes to the same bits. A program with no row is solved exactly, and a
  row whose variables are all fixed holds or is its own Farkas certificate.
* **CVaR** (Rockafellar and Uryasev) over 40 scenarios at level 0.9 is the mean
  of the worst four losses at the solution, and no sampled portfolio does better.
* **Bits.** The same program gives the same bits, pinned for every host.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence
from fractions import Fraction

import pytest

from alphalab.portfolio_optimizer import linear
from alphalab.portfolio_optimizer.exceptions import OptimizationError
from alphalab.portfolio_optimizer.linear import (
    LinearCertificate,
    LinearProgram,
    LinearSolution,
    solve_linear_program,
)
from alphalab.portfolio_optimizer.quadratic import LinearConstraint, SolveStatus
from tests.unit.portfolio_optimizer.lp_reference import Row, exact_vertex_optimum

TOLERANCE = 1e-9


def solve(program: LinearProgram) -> LinearSolution:
    return solve_linear_program(
        program,
        feasibility_tolerance=TOLERANCE,
        convergence_tolerance=TOLERANCE,
        max_iterations=200,
    )


def _generated(seed: int) -> tuple[LinearProgram, list[Row]]:
    rng = random.Random(seed)
    size = rng.choice([2, 3, 4])
    lower: list[float] = []
    upper: list[float] = []
    for _ in range(size):
        low, high = sorted((rng.randint(-3, 3), rng.randint(-3, 3)))
        lower.append(float(low))
        upper.append(float(high))
    equalities = rng.choice([0, 1])
    constraints: list[LinearConstraint] = []
    rows: list[Row] = []
    for index in range(equalities + rng.randint(1, 3)):
        equality = index < equalities
        coefficients = [rng.randint(-3, 3) for _ in range(size)]
        if not any(coefficients):
            coefficients[rng.randrange(size)] = 1
        bound = float(rng.randint(-4, 4))
        terms = tuple((j, float(value)) for j, value in enumerate(coefficients) if value)
        label = f"{'eq' if equality else 'ge'}[{index}]"
        constraints.append(LinearConstraint(label, terms, bound, equality))
        rows.append(([float(value) for value in coefficients], bound, equality))
    costs = tuple(float(rng.randint(-3, 3)) for _ in range(size))
    return LinearProgram(costs, tuple(constraints), tuple(lower), tuple(upper)), rows


def _violation(program: LinearProgram, x: Sequence[float]) -> float:
    worst = 0.0
    for constraint in program.constraints:
        value = sum(a * x[k] for k, a in constraint.terms)
        miss = abs(value - constraint.bound) if constraint.equality else constraint.bound - value
        worst = max(worst, miss)
    for value, low, high in zip(x, program.lower, program.upper, strict=True):
        worst = max(worst, low - value, value - high)
    return worst


def exact_farkas_margin(program: LinearProgram, multipliers: Sequence[float]) -> Fraction:
    """By how much ``multipliers`` prove that no point of the box meets every row, exactly.

    For any ``x`` meeting the rows, ``g(x) = sum_i y_i (a_i' x - b_i)`` is zero on
    an equality and at least ``min(0, y_i S_i)`` on an inequality, ``S_i`` the
    most ``a_i' x - b_i`` reaches over the box. So a positive ``floor - max g``
    over the box proves there is no such ``x``. Rational arithmetic throughout.
    """

    low = [Fraction(value) for value in program.lower]
    high = [Fraction(value) for value in program.upper]
    gradient = [Fraction(0)] * len(program.costs)
    constant = Fraction(0)
    floor = Fraction(0)
    for constraint, value in zip(program.constraints, multipliers, strict=True):
        y = Fraction(value)
        if y == 0:
            continue
        bound = Fraction(constraint.bound)
        for k, a in constraint.terms:
            gradient[k] += y * Fraction(a)
        constant -= y * bound
        if not constraint.equality and y < 0:
            most = sum(
                (max(Fraction(a) * low[k], Fraction(a) * high[k]) for k, a in constraint.terms),
                Fraction(0),
            )
            floor += y * max(most - bound, Fraction(0))
    top = constant + sum(
        (max(g * lo, g * hi) for g, lo, hi in zip(gradient, low, high, strict=True)), Fraction(0)
    )
    return floor - top


# --------------------------------------------------------------------------- #
# Generated programs against the exact vertex enumeration
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", range(60))
def test_a_generated_program_agrees_with_the_exact_vertex_optimum(seed: int) -> None:
    program, rows = _generated(seed)
    exact = exact_vertex_optimum(program.costs, rows, program.lower, program.upper)
    solution = solve(program)
    if exact is None:
        assert solution.status is SolveStatus.INFEASIBLE, solution.detail
        assert solution.certificate is LinearCertificate.FARKAS
        assert solution.conflict and solution.gap > 0.0
        # The certificate itself proves it, checked here in exact arithmetic.
        assert exact_farkas_margin(program, solution.multipliers) > 0
        return
    value, _ = exact
    assert solution.status is SolveStatus.OPTIMAL, solution.detail
    assert solution.x is not None and solution.objective is not None
    assert solution.bound is not None
    assert abs(Fraction(solution.objective) - value) <= Fraction(1e-7) * (1 + abs(value))
    assert _violation(program, solution.x) <= 1e-8
    # The bound is a proven lower bound: never above the exact optimum.
    assert Fraction(solution.bound) <= value + Fraction(1e-9) * (1 + abs(value))


def test_the_generated_programs_reach_both_outcomes_and_both_certificates() -> None:
    statuses: set[SolveStatus] = set()
    certificates: set[LinearCertificate | None] = set()
    for seed in range(60):
        solution = solve(_generated(seed)[0])
        statuses.add(solution.status)
        certificates.add(solution.certificate)
    assert {SolveStatus.OPTIMAL, SolveStatus.INFEASIBLE} <= statuses
    assert {LinearCertificate.VERTEX, LinearCertificate.FARKAS} <= certificates


# --------------------------------------------------------------------------- #
# Hand cases
# --------------------------------------------------------------------------- #

BUDGET = LinearConstraint("budget", ((0, 1.0), (1, 1.0)), 1.0, True)


def test_a_degenerate_vertex_is_certified_at_the_vertex_exactly() -> None:
    # min x1 + 2 x2, x1 + x2 = 1 over [0, 1]^2: the vertex (1, 0), x1 basic at its bound.
    solution = solve(LinearProgram((1.0, 2.0), (BUDGET,), (0.0, 0.0), (1.0, 1.0)))
    assert solution.status is SolveStatus.OPTIMAL
    assert solution.certificate is LinearCertificate.VERTEX
    assert solution.x == (1.0, 0.0) and solution.objective == 1.0 and solution.gap == 0.0


def test_an_optimal_edge_is_finished_at_one_of_its_vertices() -> None:
    # min -x1 - x2, x1 + x2 <= 1: every point of the edge is optimal, so the
    # iterate is inside it; the finish takes the first independent column, x1.
    cap = LinearConstraint("cap", ((0, -1.0), (1, -1.0)), -1.0, False)
    solution = solve(LinearProgram((-1.0, -1.0), (cap,), (0.0, 0.0), (1.0, 1.0)))
    assert solution.status is SolveStatus.OPTIMAL
    assert solution.certificate is LinearCertificate.VERTEX
    assert solution.x == (1.0, 0.0) and solution.objective == -1.0 and solution.gap == 0.0


def test_dependent_rows_are_certified_at_the_interior() -> None:
    # The second row is twice the first: no two columns are independent, so no
    # basis exists, and the interior iterate is certified within the tolerances.
    rows = (BUDGET, LinearConstraint("twice", ((0, 2.0), (1, 2.0)), 2.0, True))
    program = LinearProgram((1.0, 2.0), rows, (0.0, 0.0), (1.0, 1.0))
    solution = solve(program)
    assert solution.status is SolveStatus.OPTIMAL and solution.x is not None
    assert solution.certificate is LinearCertificate.INTERIOR
    assert solution.objective == pytest.approx(1.0, abs=1e-8)
    assert solution.gap <= TOLERANCE and _violation(program, solution.x) <= TOLERANCE


def test_a_program_with_no_row_is_solved_exactly() -> None:
    # Each variable at the bound its cost prefers; a zero cost keeps the lower.
    solution = solve(LinearProgram((1.0, -1.0, 0.0), (), (0.0, -2.0, 1.0), (1.0, 3.0, 1.0)))
    assert solution.status is SolveStatus.OPTIMAL
    assert solution.certificate is LinearCertificate.VERTEX
    assert solution.x == (0.0, 3.0, 1.0) and solution.objective == -3.0
    assert solution.bound == -3.0 and solution.iterations == 0 and solution.multipliers == ()


def test_a_row_of_fixed_variables_holds_or_is_its_own_farkas_certificate() -> None:
    held = solve(LinearProgram((1.0, 2.0), (BUDGET,), (0.25, 0.75), (0.25, 0.75)))
    assert held.status is SolveStatus.OPTIMAL and held.x == (0.25, 0.75)
    assert held.multipliers == (0.0,) and held.objective == held.bound == 1.75
    # 0.75 + 0.5 = 1.25 misses the budget of one from above (weight -1); the
    # floor x0 >= 0.5 holds. Nothing is iterated.
    floor = LinearConstraint("floor", ((0, 1.0),), 0.5, False)
    missed = solve(LinearProgram((1.0, 2.0), (BUDGET, floor), (0.75, 0.5), (0.75, 0.5)))
    assert missed.status is SolveStatus.INFEASIBLE
    assert missed.certificate is LinearCertificate.FARKAS and missed.iterations == 0
    assert missed.conflict == ("budget",) and missed.multipliers == (-1.0, 0.0)
    assert missed.gap == 0.25 and missed.x is None
    program = LinearProgram((1.0, 2.0), (BUDGET, floor), (0.75, 0.5), (0.75, 0.5))
    assert exact_farkas_margin(program, missed.multipliers) == Fraction(1, 4)


def test_an_infeasible_row_is_proven_and_named() -> None:
    need = LinearConstraint("need", ((0, 1.0), (1, 1.0)), 3.0, False)
    solution = solve(LinearProgram((1.0, 1.0), (need,), (0.0, 0.0), (1.0, 1.0)))
    assert solution.status is SolveStatus.INFEASIBLE
    assert solution.certificate is LinearCertificate.FARKAS
    assert solution.conflict == ("need",) and solution.x is None
    # The row cannot be met by 1: the box reaches 2 where it asks for 3.
    assert solution.gap == pytest.approx(1.0, abs=1e-6)
    assert solution.multipliers == (solution.multipliers[0],) and solution.multipliers[0] > 0.0
    assert exact_farkas_margin(
        LinearProgram((1.0, 1.0), (need,), (0.0, 0.0), (1.0, 1.0)), solution.multipliers
    ) > Fraction(1, 2)


@pytest.mark.parametrize(
    ("scale", "excess", "status"),
    [
        (1.0, 3e-10, SolveStatus.OPTIMAL),
        (1.0, 3e-9, SolveStatus.INFEASIBLE),
        (1000.0, 3e-7, SolveStatus.INFEASIBLE),
    ],
    ids=["within", "beyond", "beyond-in-its-units"],
)
def test_the_tolerance_is_in_each_rows_own_units(
    scale: float, excess: float, status: SolveStatus
) -> None:
    # scale (x0 + x1) >= 2 scale + excess over [0, 1]^2 misses by ``excess`` at best:
    # within the tolerance that is feasible, beyond it no point is, and the
    # certificate proves it -- whatever the row's scale.
    row = LinearConstraint("reach", ((0, scale), (1, scale)), 2.0 * scale + excess, False)
    program = LinearProgram((1.0, 1.0), (row,), (0.0, 0.0), (1.0, 1.0))
    solution = solve(program)
    assert solution.status is status
    if status is SolveStatus.OPTIMAL:
        assert solution.max_violation <= TOLERANCE
        return
    assert solution.conflict == ("reach",)
    weight = sum(abs(Fraction(value)) for value in solution.multipliers)
    assert exact_farkas_margin(program, solution.multipliers) > Fraction(TOLERANCE) * weight


def test_an_infeasible_program_is_proven_without_spending_the_budget() -> None:
    # Its primal residual stops at the least violation: after twenty iterations
    # without halving, Phase I proves it, long before the 200 allowed.
    need = LinearConstraint("need", ((0, 1.0), (1, 1.0)), 3.0, False)
    solution = solve(LinearProgram((1.0, 1.0), (need,), (0.0, 0.0), (1.0, 1.0)))
    assert solution.status is SolveStatus.INFEASIBLE
    assert solution.iterations < 60


def test_a_stalled_program_that_is_feasible_resumes_where_it_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stall window of one iteration stops this feasible program at once; Phase I
    # proves no infeasibility, and the interior point resumes from its iterate, so
    # it takes the steps it would have taken: the same bits, more iterations.
    program = LinearProgram(
        (-3.0, 2.0, 1.0, 3.0),
        (
            LinearConstraint("ge[0]", ((0, 1.0), (1, 1.0), (2, -1.0), (3, 2.0)), 4.0, False),
            LinearConstraint("ge[1]", ((2, -1.0), (3, 2.0)), -3.0, False),
            LinearConstraint("ge[2]", ((0, -1.0), (2, -1.0), (3, 1.0)), 3.0, False),
        ),
        (-2.0, -2.0, -1.0, 0.0),
        (2.0, 3.0, 3.0, 2.0),
    )
    uninterrupted = solve(program)
    monkeypatch.setattr(linear, "_STALL", 1)
    resumed = solve(program)
    assert uninterrupted.status is resumed.status is SolveStatus.OPTIMAL
    assert resumed.x == uninterrupted.x and resumed.objective == uninterrupted.objective
    assert resumed.iterations > uninterrupted.iterations


def test_shifted_and_fixed_bounds_are_the_programs_own() -> None:
    # min x0 - x1 + x2, x0 + x1 + x2 = 2, x1 fixed at 0.5: x0 + x2 = 1.5, objective 1.
    row = LinearConstraint("sum", ((0, 1.0), (1, 1.0), (2, 1.0)), 2.0, True)
    program = LinearProgram((1.0, -1.0, 1.0), (row,), (-1.0, 0.5, -3.0), (2.0, 0.5, 3.0))
    solution = solve(program)
    assert solution.status is SolveStatus.OPTIMAL and solution.x is not None
    assert solution.x[1] == 0.5
    assert solution.objective == pytest.approx(1.0, abs=1e-8)
    assert solution.bound is not None and solution.objective is not None
    assert solution.bound <= solution.objective + 1e-12
    assert _violation(program, solution.x) <= 1e-8


# --------------------------------------------------------------------------- #
# CVaR, by Rockafellar and Uryasev
# --------------------------------------------------------------------------- #

ASSETS, SCENARIOS, LEVEL = 5, 40, 0.9


def _scenarios() -> list[list[float]]:
    rng = random.Random(3)
    return [[rng.gauss(0.001, 0.02) for _ in range(ASSETS)] for _ in range(SCENARIOS)]


def _cvar_program(returns: list[list[float]]) -> LinearProgram:
    """Weights w (long only, fully invested), alpha, and one excess loss u_s per scenario."""

    tail = 1.0 / ((1.0 - LEVEL) * SCENARIOS)
    costs = (0.0,) * ASSETS + (1.0,) + (tail,) * SCENARIOS
    rows = [LinearConstraint("budget", tuple((j, 1.0) for j in range(ASSETS)), 1.0, True)]
    for s, row in enumerate(returns):
        terms = [(j, value) for j, value in enumerate(row)] + [(ASSETS, 1.0), (ASSETS + 1 + s, 1.0)]
        rows.append(LinearConstraint(f"tail[{s}]", tuple(terms), 0.0, False))
    lower = (0.0,) * ASSETS + (-1.0,) + (0.0,) * SCENARIOS
    upper = (1.0,) * ASSETS + (1.0,) + (2.0,) * SCENARIOS
    return LinearProgram(costs, tuple(rows), lower, upper)


def _tail_mean(returns: list[list[float]], weights: Sequence[float]) -> float:
    losses = sorted(
        (-sum(r * w for r, w in zip(row, weights, strict=True)) for row in returns), reverse=True
    )
    worst = round((1.0 - LEVEL) * SCENARIOS)
    return sum(losses[:worst]) / worst


def test_cvar_is_the_mean_of_the_worst_losses_and_no_sampled_portfolio_does_better() -> None:
    returns = _scenarios()
    solution = solve(_cvar_program(returns))
    assert solution.status is SolveStatus.OPTIMAL and solution.x is not None
    assert solution.objective is not None
    weights = solution.x[:ASSETS]
    assert sum(weights) == pytest.approx(1.0, abs=1e-9)
    # At level 0.9 over 40 scenarios CVaR is the mean of the worst four losses.
    assert solution.objective == pytest.approx(_tail_mean(returns, weights), abs=1e-9)
    rng = random.Random(11)
    for _ in range(2000):
        draws = [rng.expovariate(1.0) for _ in range(ASSETS)]
        total = sum(draws)
        assert _tail_mean(returns, [d / total for d in draws]) >= solution.objective - 1e-12


def test_the_same_program_gives_the_same_bits_on_every_host() -> None:
    returns = _scenarios()
    first = solve(_cvar_program(returns))
    again = solve(_cvar_program(returns))
    assert first.x == again.x and first.objective == again.objective
    digest = hashlib.sha256(repr((first.x, first.objective)).encode()).hexdigest()
    assert digest == PINNED_CVAR


#: The CVaR program's solution, ``repr`` of its weights, alpha, excesses and objective.
PINNED_CVAR = "9993a77ed00ed8325aed88c7861dcd662450831980c566787076b2c813caab01"


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (((), (), (), ()), "at least one variable"),
        (((1.0,), (), (0.0, 0.0), (1.0,)), "needs 1 lower and upper"),
        (((float("nan"),), (), (0.0,), (1.0,)), "must be finite"),
        (((1.0,), (), (2.0,), (1.0,)), "above its upper bound"),
        (
            ((1.0,), (LinearConstraint("far", ((3, 1.0),), 0.0, True),), (0.0,), (1.0,)),
            "names variable 3",
        ),
    ],
    ids=["empty", "mismatched", "nan", "crossed", "out-of-range"],
)
def test_a_malformed_program_is_refused(arguments: tuple[object, ...], reason: str) -> None:
    with pytest.raises(OptimizationError, match=reason):
        LinearProgram(*arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "settings",
    [
        {"feasibility_tolerance": 0.0, "convergence_tolerance": 1e-9, "max_iterations": 10},
        {"feasibility_tolerance": 1e-9, "convergence_tolerance": 1.0, "max_iterations": 10},
        {"feasibility_tolerance": 1e-9, "convergence_tolerance": 1e-9, "max_iterations": 0},
    ],
    ids=["feasibility", "convergence", "iterations"],
)
def test_settings_outside_their_range_are_refused(settings: dict[str, float]) -> None:
    program = LinearProgram((1.0, 2.0), (BUDGET,), (0.0, 0.0), (1.0, 1.0))
    with pytest.raises(OptimizationError):
        solve_linear_program(program, **settings)  # type: ignore[arg-type]
