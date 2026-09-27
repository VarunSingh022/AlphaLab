"""The dual active-set solver, against an independent exact reference.

The reference enumerates every candidate active set, solves each one's KKT
system in exact rational arithmetic (``fractions.Fraction``), and keeps the one
point that is primal feasible with non-negative inequality multipliers -- the
unique optimum of a strictly convex program -- or finds none, which proves the
program infeasible. It shares no code with the solver.
"""

from __future__ import annotations

import itertools
import math
import random

import pytest

from alphalab.portfolio_optimizer.exceptions import OptimizationError
from alphalab.portfolio_optimizer.quadratic import (
    AbsoluteSumLimit,
    LinearConstraint,
    QuadraticProgram,
    SolveStatus,
    constraint_residuals,
    solve_quadratic_program,
)
from tests.unit.portfolio_optimizer.qp_reference import Row, exact_optimum

G = ((0.04, 0.01, 0.0), (0.01, 0.09, 0.02), (0.0, 0.02, 0.16))
ZERO = (0.0, 0.0, 0.0)
BUDGET = LinearConstraint("budget", ((0, 1.0), (1, 1.0), (2, 1.0)), 1.0, True)


def solve(program: QuadraticProgram, iterations: int = 500):  # type: ignore[no-untyped-def]
    return solve_quadratic_program(
        program, feasibility_tolerance=1e-10, convergence_tolerance=1e-10, max_iterations=iterations
    )


# --------------------------------------------------------------------------- #
# The exact reference
# --------------------------------------------------------------------------- #


def _random_program(seed: int) -> tuple[QuadraticProgram, list[Row]]:
    rng = random.Random(seed)
    size = rng.randint(1, 4)
    basis = [[rng.gauss(0.0, 1.0) for _ in range(size)] for _ in range(size)]
    hessian = [
        [
            sum(basis[i][k] * basis[j][k] for k in range(size)) + (0.5 if i == j else 0.0)
            for j in range(size)
        ]
        for i in range(size)
    ]
    for i in range(size):
        for j in range(i):
            hessian[i][j] = hessian[j][i]
    linear = [rng.gauss(0.0, 1.0) for _ in range(size)]
    constraints: list[LinearConstraint] = []
    rows: list[Row] = []
    for index in range(rng.randint(0, 5)):
        members = sorted(rng.sample(range(size), rng.randint(1, size)))
        coefficients = [rng.choice([-2.0, -1.0, -0.5, 0.5, 1.0, 2.0]) for _ in members]
        bound = rng.uniform(-1.0, 1.0)
        equality = rng.random() < 0.2
        constraints.append(
            LinearConstraint(
                f"c{index}", tuple(zip(members, coefficients, strict=True)), bound, equality
            )
        )
        normal = [0.0] * size
        for member, coefficient in zip(members, coefficients, strict=True):
            normal[member] = coefficient
        rows.append((normal, bound, equality))
    families: list[AbsoluteSumLimit] = []
    if size <= 3 and rng.random() < 0.5:
        centers = tuple(rng.uniform(-0.5, 0.5) for _ in range(size))
        limit = rng.uniform(0.1, 2.0)
        families.append(AbsoluteSumLimit("l1", tuple(range(size)), centers, limit))
        for signs in itertools.product((-1, 1), repeat=size):
            rows.append(
                (
                    [-float(sign) for sign in signs],
                    -limit
                    - sum(sign * center for sign, center in zip(signs, centers, strict=True)),
                    False,
                )
            )
    program = QuadraticProgram(
        tuple(tuple(row) for row in hessian), tuple(linear), tuple(constraints), tuple(families)
    )
    return program, rows


@pytest.mark.parametrize("block", range(8))
def test_the_solver_agrees_with_exhaustive_exact_enumeration(block: int) -> None:
    infeasible = 0
    for seed in range(block * 40, block * 40 + 40):
        program, rows = _random_program(seed)
        reference = exact_optimum(program.hessian, program.linear, rows)
        solution = solve(program)
        if reference is None:
            infeasible += 1
            assert solution.status is SolveStatus.INFEASIBLE, seed
            assert solution.x is None
            assert solution.conflict, seed
        else:
            assert solution.status is SolveStatus.OPTIMAL, (seed, solution.detail)
            assert solution.x is not None
            assert max(abs(a - b) for a, b in zip(solution.x, reference, strict=True)) < 1e-8, seed
    assert infeasible > 0, "every block should exercise the infeasible branch"


# --------------------------------------------------------------------------- #
# Degenerate and named cases
# --------------------------------------------------------------------------- #


def _bounds(upper: float) -> tuple[LinearConstraint, ...]:
    lower = tuple(LinearConstraint(f"w{i} >= 0", ((i, 1.0),), 0.0, False) for i in range(3))
    capped = tuple(
        LinearConstraint(f"w{i} <= {upper}", ((i, -1.0),), -upper, False) for i in range(3)
    )
    return lower + capped


def test_the_budget_alone_gives_the_closed_form_minimum_variance() -> None:
    solution = solve(QuadraticProgram(G, ZERO, (BUDGET,)))

    # w = C^-1 1 / 1' C^-1 1, by hand for this C.
    assert solution.status is SolveStatus.OPTIMAL
    assert solution.x == pytest.approx(
        (0.6461538461538462, 0.20512820512820512, 0.14871794871794872)
    )
    assert solution.active == ("budget",)
    assert solution.multipliers["budget"] > 0.0


def test_the_unconstrained_minimum_is_minus_g_inverse_a() -> None:
    solution = solve(QuadraticProgram(((2.0, 0.0), (0.0, 4.0)), (-2.0, 4.0)))

    assert solution.x == pytest.approx((1.0, -1.0))
    assert solution.objective == pytest.approx(0.5 * (2 * 1 + 4 * 1) - 2 - 4)
    assert solution.active == ()


def test_a_redundant_consistent_equality_is_skipped() -> None:
    twice = LinearConstraint("budget again", ((0, 2.0), (1, 2.0), (2, 2.0)), 2.0, True)

    assert (
        solve(QuadraticProgram(G, ZERO, (BUDGET, twice))).x
        == solve(QuadraticProgram(G, ZERO, (BUDGET,))).x
    )


def test_inconsistent_equalities_are_infeasible_with_both_named() -> None:
    other = LinearConstraint("budget 0.9", ((0, 1.0), (1, 1.0), (2, 1.0)), 0.9, True)
    solution = solve(QuadraticProgram(G, ZERO, (BUDGET, other)))

    assert solution.status is SolveStatus.INFEASIBLE
    assert set(solution.conflict) == {"budget", "budget 0.9"}


def test_caps_that_cannot_reach_the_budget_are_infeasible_with_a_minimal_witness() -> None:
    solution = solve(QuadraticProgram(G, ZERO, (BUDGET, *_bounds(0.3))))

    assert solution.status is SolveStatus.INFEASIBLE
    assert set(solution.conflict) == {"budget", "w0 <= 0.3", "w1 <= 0.3", "w2 <= 0.3"}


def test_caps_that_just_reach_the_budget_bind_exactly() -> None:
    upper = 1.0 / 3.0
    solution = solve(QuadraticProgram(G, ZERO, (BUDGET, *_bounds(upper))))

    assert solution.status is SolveStatus.OPTIMAL
    assert solution.x == pytest.approx((upper, upper, upper), abs=1e-12)


def test_duplicated_constraints_do_not_confuse_the_active_set() -> None:
    lower = _bounds(1.0)[:3]
    solution = solve(QuadraticProgram(G, ZERO, (BUDGET, *lower, *lower)))

    assert solution.status is SolveStatus.OPTIMAL
    assert solution.x == pytest.approx(solve(QuadraticProgram(G, ZERO, (BUDGET,))).x)


def test_a_gross_limit_below_the_net_budget_is_infeasible() -> None:
    gross = AbsoluteSumLimit("gross", (0, 1, 2), ZERO, 0.99)
    solution = solve(QuadraticProgram(G, ZERO, (BUDGET,), (gross,)))

    assert solution.status is SolveStatus.INFEASIBLE
    assert set(solution.conflict) == {"gross", "budget"}


def test_a_turnover_limit_binds_and_is_satisfied_exactly() -> None:
    turnover = AbsoluteSumLimit("turnover", (0, 1, 2), (1.0, 0.0, 0.0), 0.2)
    solution = solve(QuadraticProgram(G, ZERO, (BUDGET,), (turnover,)))

    assert solution.status is SolveStatus.OPTIMAL
    assert solution.x is not None
    assert math.fsum(abs(a - b) for a, b in zip(solution.x, (1.0, 0.0, 0.0), strict=True)) == (
        pytest.approx(0.2, abs=1e-10)
    )
    assert "turnover" in solution.active
    assert solution.multipliers["turnover"] > 0.0


def test_a_negative_constraint_multiplier_is_never_reported_for_an_inequality() -> None:
    solution = solve(QuadraticProgram(G, (-0.1, 0.2, -0.05), (BUDGET, *_bounds(0.5))))

    assert solution.status is SolveStatus.OPTIMAL
    for label, value in solution.multipliers.items():
        if label != "budget":
            assert value >= 0.0


def test_the_certificate_is_small_on_an_optimal_answer() -> None:
    solution = solve(QuadraticProgram(G, (-0.1, 0.2, -0.05), (BUDGET, *_bounds(0.5))))

    assert solution.max_violation <= 1e-10
    assert solution.stationarity <= 1e-10
    assert 0.0 < solution.pivot_ratio <= 1.0


def test_a_hessian_that_is_not_positive_definite_is_a_numerical_failure() -> None:
    singular = ((1.0, 1.0), (1.0, 1.0))
    solution = solve(QuadraticProgram(singular, (0.0, 0.0)))

    assert solution.status is SolveStatus.NUMERICAL_FAILURE
    assert solution.x is None
    assert "positive definite" in solution.detail


def test_the_step_budget_is_enforced_and_reported() -> None:
    solution = solve(QuadraticProgram(G, (-0.1, 0.2, -0.05), (BUDGET, *_bounds(0.5))), iterations=1)

    assert solution.status is SolveStatus.ITERATION_LIMIT
    assert solution.x is None
    assert solution.iterations == 1


def test_the_same_program_gives_the_same_bits() -> None:
    program = QuadraticProgram(
        G,
        (-0.1, 0.2, -0.05),
        (BUDGET, *_bounds(0.5)),
        (AbsoluteSumLimit("turnover", (0, 1, 2), (0.2, 0.5, 0.3), 0.3),),
    )

    assert solve(program) == solve(program)


# --------------------------------------------------------------------------- #
# Residuals
# --------------------------------------------------------------------------- #


def test_constraint_residuals_measure_each_constraint_in_its_own_units() -> None:
    program = QuadraticProgram(
        G, ZERO, (BUDGET, *_bounds(0.3)), (AbsoluteSumLimit("gross", (0, 1, 2), ZERO, 0.9),)
    )
    residuals = constraint_residuals(program, (0.5, 0.25, 0.25))

    assert residuals["budget"] == 0.0
    assert residuals["w0 <= 0.3"] == pytest.approx(0.2)
    assert residuals["w1 <= 0.3"] == 0.0
    assert residuals["gross"] == pytest.approx(0.1)


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("terms", "message"),
    [
        ((), "no terms"),
        (((1, 1.0), (0, 1.0)), "strictly increasing"),
        (((0, 0.0),), "zero coefficient"),
        (((0, float("nan")),), "finite"),
        (((True, 1.0),), "integer"),
    ],
)
def test_malformed_linear_constraints_are_refused(
    terms: tuple[tuple[int, float], ...], message: str
) -> None:
    with pytest.raises(OptimizationError, match=message):
        LinearConstraint("c", terms, 0.0, False)


def test_a_constraint_is_labelled() -> None:
    with pytest.raises(OptimizationError, match="labelled"):
        LinearConstraint(" ", ((0, 1.0),), 0.0, False)


def test_malformed_absolute_sum_limits_are_refused() -> None:
    with pytest.raises(OptimizationError, match="one center per index"):
        AbsoluteSumLimit("g", (0, 1), (0.0,), 1.0)
    with pytest.raises(OptimizationError, match="increasing"):
        AbsoluteSumLimit("g", (1, 0), (0.0, 0.0), 1.0)
    with pytest.raises(OptimizationError, match="negative"):
        AbsoluteSumLimit("g", (0,), (0.0,), -1.0)
    with pytest.raises(OptimizationError, match="labelled"):
        AbsoluteSumLimit("", (0,), (0.0,), 1.0)


def test_malformed_programs_are_refused() -> None:
    with pytest.raises(OptimizationError, match="at least one variable"):
        QuadraticProgram((), ())
    with pytest.raises(OptimizationError, match="must be 2 x 2"):
        QuadraticProgram(((1.0,),), (0.0, 0.0))
    with pytest.raises(OptimizationError, match="not symmetric"):
        QuadraticProgram(((1.0, 0.5), (0.4, 1.0)), (0.0, 0.0))
    with pytest.raises(OptimizationError, match="reaches index 3"):
        QuadraticProgram(G, ZERO, (LinearConstraint("c", ((3, 1.0),), 0.0, False),))
    with pytest.raises(OptimizationError, match="reaches index 5"):
        QuadraticProgram(G, ZERO, (), (AbsoluteSumLimit("g", (5,), (0.0,), 1.0),))


@pytest.mark.parametrize(
    ("feasibility", "convergence", "iterations"),
    [(0.0, 1e-9, 10), (1e-9, 1.0, 10), (1e-9, 1e-9, 0), (float("nan"), 1e-9, 10)],
)
def test_the_numerical_contract_is_validated(
    feasibility: float, convergence: float, iterations: int
) -> None:
    with pytest.raises(OptimizationError):
        solve_quadratic_program(
            QuadraticProgram(G, ZERO),
            feasibility_tolerance=feasibility,
            convergence_tolerance=convergence,
            max_iterations=iterations,
        )
