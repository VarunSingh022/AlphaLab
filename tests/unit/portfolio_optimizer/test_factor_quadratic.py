"""The factor-structured interior-point solver (ledger PRF-005), against two references.

Small programs are checked against the exact rational reference
(:mod:`tests.unit.portfolio_optimizer.qp_reference`), which shares no code with
either solver. Larger ones -- up to 70 variables, with bounds, groups, factor
rows, gross and turnover limits and degenerate vertices -- are checked against
the dense dual active-set solver, which is exact by construction and proves
infeasibility. The structured method must agree with both, never certify a
point the dense method would not, and never claim infeasibility.
"""

from __future__ import annotations

import itertools
import math
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from alphalab.analytics.risk_model import CovarianceMatrix, FactorLoadings
from alphalab.portfolio_optimizer.exceptions import OptimizationError
from alphalab.portfolio_optimizer.factor_quadratic import (
    FactorCurvature,
    FactorQuadraticProgram,
    solve_factor_quadratic_program,
)
from alphalab.portfolio_optimizer.quadratic import (
    AbsoluteSumLimit,
    LinearConstraint,
    QuadraticProgram,
    SolveStatus,
    constraint_residuals,
    solve_quadratic_program,
)
from tests.unit.portfolio_optimizer.qp_reference import Row, exact_optimum

ROOT = Path(__file__).resolve().parents[3]

ONE_FACTOR = FactorCurvature(((1.0,), (0.5,), (-0.5,)), ((0.04,),), (0.01, 0.02, 0.03), 1.0)


def solve(program: FactorQuadraticProgram, iterations: int = 500):  # type: ignore[no-untyped-def]
    return solve_factor_quadratic_program(
        program, feasibility_tolerance=1e-10, convergence_tolerance=1e-10, max_iterations=iterations
    )


def dense(curvature: FactorCurvature) -> tuple[tuple[float, ...], ...]:
    """``scale * (B F B' + D)`` written out, cell by cell."""

    b, f = curvature.loadings, curvature.factor_covariance
    size, width = len(b), len(f)
    rows = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i, size):
            value = math.fsum(
                b[i][g] * f[g][h] * b[j][h] for g in range(width) for h in range(width)
            )
            if i == j:
                value += curvature.specific[i]
            rows[i][j] = rows[j][i] = curvature.scale * value
    return tuple(tuple(row) for row in rows)


# --------------------------------------------------------------------------- #
# The data
# --------------------------------------------------------------------------- #


def test_the_curvature_multiplies_like_its_dense_matrix() -> None:
    rng = random.Random(4)
    curvature = FactorCurvature(
        tuple(tuple(rng.gauss(0, 1) for _ in range(2)) for _ in range(6)),
        ((0.04, 0.01), (0.01, 0.09)),
        tuple(0.01 + 0.01 * rng.random() for _ in range(6)),
        2.5,
    )
    x = [rng.gauss(0, 1) for _ in range(6)]
    rows = dense(curvature)

    expected = [math.fsum(row[j] * x[j] for j in range(6)) for row in rows]
    assert curvature.times(x) == pytest.approx(expected, rel=1e-13, abs=1e-15)
    assert 0.0 < curvature.pivot_ratio <= 1.0


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (((), ((1.0,),), (), 1.0), "at least one variable"),
        ((((1.0,),), ((1.0,),), (0.1, 0.2), 1.0), "must be 2 x 1"),
        ((((1.0,),), ((1.0, 0.0),), (0.1,), 1.0), "must be square"),
        ((((1.0, 2.0),), ((1.0, 0.1), (0.2, 1.0)), (0.1,), 1.0), "not symmetric"),
        ((((1.0,),), ((math.nan,),), (0.1,), 1.0), "finite"),
        ((((1.0,),), ((1.0,),), (0.0,), 1.0), "positive"),
        ((((1.0,),), ((1.0,),), (0.1,), 0.0), "must be positive"),
        ((((1.0, 1.0),), ((1.0, 2.0), (2.0, 1.0)), (0.1,), 1.0), "not positive semidefinite"),
    ],
)
def test_a_malformed_curvature_is_refused(arguments: tuple[object, ...], message: str) -> None:
    with pytest.raises(OptimizationError, match=message):
        FactorCurvature(*arguments)  # type: ignore[arg-type]


def test_a_semidefinite_factor_covariance_is_accepted() -> None:
    # Rank one: two factors that move together exactly. D still makes G definite.
    curvature = FactorCurvature(
        ((1.0, 0.0), (0.0, 1.0), (1.0, 1.0)), ((0.04, 0.04), (0.04, 0.04)), (0.01,) * 3, 1.0
    )

    solution = solve(FactorQuadraticProgram(curvature, (0.0, 0.0, 0.0), (_budget(3),)))
    assert solution.status is SolveStatus.OPTIMAL


def test_a_structure_becomes_a_curvature_without_being_checked_again() -> None:
    loadings = FactorLoadings.of(
        {"A": {"f": 1.0}, "B": {"f": 0.5}, "C": {"f": -0.5}},
        source="test",
        lineage={"f": "test"},
        as_of=None,
    )
    factor = CovarianceMatrix.from_rows(
        ["f"], [[0.04]], currency="USD", period="1D", source="test", observations=None
    )
    covariance = CovarianceMatrix.factor_model(loadings, factor, {"A": 0.01, "B": 0.02, "C": 0.03})
    assert covariance.factors is not None

    built = FactorCurvature.of_structure(covariance.factors, 1.0)
    assert (built.loadings, built.factor_covariance, built.specific, built.scale) == (
        ONE_FACTOR.loadings,
        ONE_FACTOR.factor_covariance,
        ONE_FACTOR.specific,
        1.0,
    )
    assert built.pivot_ratio == ONE_FACTOR.pivot_ratio
    assert built.columns == ONE_FACTOR.columns
    with pytest.raises(OptimizationError, match="must be positive"):
        FactorCurvature.of_structure(covariance.factors, -1.0)


def test_a_mis_sized_program_is_refused() -> None:
    with pytest.raises(OptimizationError, match="2 linear coefficients"):
        FactorQuadraticProgram(ONE_FACTOR, (0.0, 0.0))
    with pytest.raises(OptimizationError, match="reaches index 3"):
        FactorQuadraticProgram(
            ONE_FACTOR, (0.0,) * 3, (LinearConstraint("far", ((3, 1.0),), 0.0, False),)
        )
    with pytest.raises(OptimizationError, match="reaches index 5"):
        FactorQuadraticProgram(
            ONE_FACTOR, (0.0,) * 3, (), (AbsoluteSumLimit("far", (5,), (0.0,), 1.0),)
        )


@pytest.mark.parametrize(
    ("keywords", "message"),
    [
        ({"feasibility_tolerance": 0.0}, "feasibility_tolerance"),
        ({"convergence_tolerance": 1.0}, "convergence_tolerance"),
        ({"max_iterations": 0}, "max_iterations"),
        ({"max_iterations": True}, "max_iterations"),
    ],
)
def test_invalid_settings_are_refused(keywords: dict[str, object], message: str) -> None:
    settings: dict[str, object] = {
        "feasibility_tolerance": 1e-9,
        "convergence_tolerance": 1e-9,
        "max_iterations": 10,
        **keywords,
    }
    with pytest.raises(OptimizationError, match=message):
        solve_factor_quadratic_program(
            FactorQuadraticProgram(ONE_FACTOR, (0.0,) * 3),
            **settings,  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------- #
# Against the exact reference
# --------------------------------------------------------------------------- #


def _budget(size: int) -> LinearConstraint:
    return LinearConstraint("budget", tuple((index, 1.0) for index in range(size)), 1.0, True)


def _small_program(seed: int) -> tuple[FactorQuadraticProgram, list[Row]]:
    rng = random.Random(seed)
    size = rng.randint(1, 4)
    width = rng.randint(1, 2)
    root = [[rng.gauss(0.0, 0.3) for _ in range(width)] for _ in range(width)]
    factor = [
        [sum(root[g][m] * root[h][m] for m in range(width)) for h in range(width)]
        for g in range(width)
    ]
    for g in range(width):
        for h in range(g):
            factor[g][h] = factor[h][g]
    curvature = FactorCurvature(
        tuple(tuple(rng.gauss(0.0, 1.0) for _ in range(width)) for _ in range(size)),
        tuple(tuple(row) for row in factor),
        tuple(0.05 + 0.5 * rng.random() for _ in range(size)),
        rng.choice([0.5, 1.0, 4.0]),
    )
    linear = tuple(rng.gauss(0.0, 1.0) for _ in range(size))
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
    return FactorQuadraticProgram(curvature, linear, tuple(constraints), tuple(families)), rows


@pytest.mark.parametrize("block", range(6))
def test_the_solver_agrees_with_exhaustive_exact_enumeration(block: int) -> None:
    infeasible = 0
    for seed in range(block * 40, block * 40 + 40):
        program, rows = _small_program(seed)
        reference = exact_optimum(dense(program.curvature), program.linear, rows)
        solution = solve(program)
        if reference is None:
            infeasible += 1
            # Never a claim of infeasibility, never a point.
            assert solution.status is not SolveStatus.OPTIMAL, seed
            assert solution.status is not SolveStatus.INFEASIBLE, seed
            assert solution.x is None
        else:
            assert solution.status is SolveStatus.OPTIMAL, (seed, solution.detail)
            assert solution.x is not None
            assert max(abs(a - b) for a, b in zip(solution.x, reference, strict=True)) < 1e-8, seed
    assert infeasible > 0, "every block should exercise the infeasible branch"


# --------------------------------------------------------------------------- #
# Against the dense solver, on construction-shaped programs
# --------------------------------------------------------------------------- #


def _construction_program(seed: int) -> FactorQuadraticProgram:
    """Long-only, boxed or dollar-neutral books with groups, factor rows and limits."""

    rng = random.Random(seed)
    size = rng.choice([3, 5, 8, 12, 20, 40, 70])
    width = rng.choice([1, 2, 3, 5])
    loadings = tuple(tuple(rng.gauss(0, 1) for _ in range(width)) for _ in range(size))
    root = [[rng.gauss(0, 0.2) for _ in range(width)] for _ in range(width)]
    factor = [
        [sum(root[i][m] * root[j][m] for m in range(width)) + (0.01 if i == j else 0.0)
         for j in range(width)]
        for i in range(width)
    ]  # fmt: skip
    for i in range(width):
        for j in range(i):
            factor[i][j] = factor[j][i]
    specific = tuple(0.005 + 0.03 * rng.random() for _ in range(size))
    scale = rng.choice([0.5, 1.0, 3.0, 10.0])
    linear = tuple(-rng.gauss(0.05, 0.1) * rng.choice([0, 1, 1, 1]) for _ in range(size))
    constraints: list[LinearConstraint] = []
    style = rng.choice(["long", "box", "free", "dollar"])
    if style in ("long", "box"):
        constraints.append(_budget(size))
    elif style == "dollar":
        constraints.append(
            LinearConstraint("budget", tuple((i, 1.0) for i in range(size)), 0.0, True)
        )
    cap = max(1.5 / size, rng.choice([0.05, 0.1, 0.2, 0.5]))
    for i in range(size):
        if style == "long":
            constraints.append(LinearConstraint(f"w{i}>=0", ((i, 1.0),), 0.0, False))
            constraints.append(LinearConstraint(f"w{i}<=cap", ((i, -1.0),), -cap, False))
        elif style in ("box", "dollar"):
            constraints.append(LinearConstraint(f"w{i}>=-c", ((i, 1.0),), -cap, False))
            constraints.append(LinearConstraint(f"w{i}<=c", ((i, -1.0),), -cap, False))
    if rng.random() < 0.5 and size >= 6:
        members = sorted(rng.sample(range(size), size // 3))
        constraints.append(
            LinearConstraint(
                "group<=g", tuple((i, -1.0) for i in members), -rng.choice([0.2, 0.3, 0.5]), False
            )
        )
    if rng.random() < 0.4:
        g = rng.randrange(width)
        terms = tuple((i, loadings[i][g]) for i in range(size) if loadings[i][g] != 0.0)
        limit = rng.choice([0.05, 0.1, 0.3])
        constraints.append(LinearConstraint("factor>=-l", terms, -limit, False))
        constraints.append(
            LinearConstraint("factor<=l", tuple((i, -v) for i, v in terms), -limit, False)
        )
    if rng.random() < 0.2:
        j = rng.randrange(size)
        constraints.append(LinearConstraint(f"fix{j}", ((j, 1.0),), 0.5 / size, True))
    families = []
    if rng.random() < 0.35:
        families.append(
            AbsoluteSumLimit(
                "gross", tuple(range(size)), (0.0,) * size, rng.choice([1.0, 1.3, 1.6])
            )
        )
    if rng.random() < 0.35:
        current = [rng.random() for _ in range(size)]
        total = sum(current)
        current = [value / total for value in current]
        if style == "dollar":
            current = [value - 1.0 / size for value in current]
        families.append(
            AbsoluteSumLimit(
                "turnover", tuple(range(size)), tuple(current), rng.choice([0.1, 0.3, 0.6])
            )
        )
    return FactorQuadraticProgram(
        FactorCurvature(loadings, tuple(tuple(row) for row in factor), specific, scale),
        linear,
        tuple(constraints),
        tuple(families),
    )


#: Each seed of this generator exposed a defect in the method's exact finish
#: while it was written: degenerate vertices whose binding rows are dependent
#: (multipliers a family, not a point), and a nearly-traded asset taken for an
#: untraded one, which over-fixed the turnover row. The rest are ordinary.
SEEDS = (
    *(74, 83, 96, 108, 111, 131, 170, 174, 188, 197),
    *(228, 233, 247, 252, 270, 287, 329, 380),
    *range(0, 24),
)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_solver_agrees_with_the_dense_method(seed: int) -> None:
    program = _construction_program(seed)
    settings = {
        "feasibility_tolerance": 1e-9,
        "convergence_tolerance": 1e-9,
        "max_iterations": 100_000,
    }
    reference = solve_quadratic_program(
        QuadraticProgram(
            dense(program.curvature), program.linear, program.constraints, program.absolute_sums
        ),
        **settings,  # type: ignore[arg-type]
    )
    solution = solve_factor_quadratic_program(program, **settings)  # type: ignore[arg-type]

    if reference.status is SolveStatus.INFEASIBLE:
        assert solution.status is SolveStatus.NUMERICAL_FAILURE, seed
        assert solution.x is None
        return
    assert reference.status is SolveStatus.OPTIMAL
    assert solution.status is SolveStatus.OPTIMAL, (seed, solution.detail)
    assert solution.x is not None and reference.x is not None
    assert max(abs(a - b) for a, b in zip(solution.x, reference.x, strict=True)) < 1e-8
    assert solution.objective is not None and reference.objective is not None
    assert solution.objective == pytest.approx(reference.objective, rel=1e-9, abs=1e-12)
    # Certified by the same rules: every constraint, every multiplier sign.
    assert max(constraint_residuals(program, solution.x).values(), default=0.0) <= 1e-9
    assert solution.stationarity <= 1e-9
    labels = {constraint.label for constraint in program.constraints if constraint.equality}
    assert all(value >= 0.0 for label, value in solution.multipliers.items() if label not in labels)


# --------------------------------------------------------------------------- #
# Named cases
# --------------------------------------------------------------------------- #


def test_an_equality_constrained_program_needs_no_interior_point() -> None:
    solution = solve(FactorQuadraticProgram(ONE_FACTOR, (0.0,) * 3, (_budget(3),)))
    reference = solve_quadratic_program(
        QuadraticProgram(dense(ONE_FACTOR), (0.0,) * 3, (_budget(3),)),
        feasibility_tolerance=1e-10,
        convergence_tolerance=1e-10,
        max_iterations=100,
    )

    assert solution.status is SolveStatus.OPTIMAL
    assert solution.iterations == 1  # one exact solve
    assert solution.x == pytest.approx(reference.x, abs=1e-12)
    assert solution.active == ("budget",)
    assert solution.multipliers["budget"] == pytest.approx(reference.multipliers["budget"])


def test_an_inconsistent_program_is_handed_back_never_called_infeasible() -> None:
    contradiction = (
        LinearConstraint("x0 >= 1", ((0, 1.0),), 1.0, False),
        LinearConstraint("x0 <= 0", ((0, -1.0),), 0.0, False),
    )

    solution = solve(FactorQuadraticProgram(ONE_FACTOR, (0.0,) * 3, contradiction))

    assert solution.status is SolveStatus.NUMERICAL_FAILURE
    assert solution.x is None and solution.objective is None
    assert "does not prove inconsistency" in solution.detail


def test_a_spent_step_budget_is_an_iteration_limit() -> None:
    bounded = tuple(LinearConstraint(f"x{i} >= 0", ((i, 1.0),), 0.0, False) for i in range(3))

    solution = solve(FactorQuadraticProgram(ONE_FACTOR, (-1.0, 0.0, 0.0), bounded), iterations=1)

    assert solution.status is SolveStatus.ITERATION_LIMIT
    assert solution.x is None


def test_scaling_the_objective_scales_the_multipliers_and_not_the_point() -> None:
    program = _construction_program(5)
    curvature = program.curvature
    larger = FactorQuadraticProgram(
        FactorCurvature(
            curvature.loadings,
            curvature.factor_covariance,
            curvature.specific,
            8.0 * curvature.scale,
        ),
        tuple(8.0 * value for value in program.linear),
        program.constraints,
        program.absolute_sums,
    )

    first, second = solve(program), solve(larger)
    assert first.x is not None and second.x is not None
    assert second.x == pytest.approx(first.x, abs=1e-10)
    for label, value in first.multipliers.items():
        assert second.multipliers[label] == pytest.approx(8.0 * value, rel=1e-6, abs=1e-12)


def test_a_turnover_limit_leaves_untraded_assets_exactly_where_they_were() -> None:
    size = 12
    rng = random.Random(9)
    current = [1.0 / size] * size
    program = FactorQuadraticProgram(
        FactorCurvature(
            tuple((rng.gauss(0, 1),) for _ in range(size)),
            ((0.04,),),
            tuple(0.01 + 0.02 * rng.random() for _ in range(size)),
            1.0,
        ),
        tuple(-rng.gauss(0.05, 0.2) for _ in range(size)),
        (_budget(size),),
        (AbsoluteSumLimit("turnover", tuple(range(size)), tuple(current), 0.2),),
    )

    solution = solve(program)
    assert solution.status is SolveStatus.OPTIMAL
    assert solution.x is not None
    untraded = [i for i in range(size) if solution.x[i] == current[i]]
    assert untraded, "a tight turnover limit leaves some assets untraded, exactly"
    assert math.fsum(abs(a - b) for a, b in zip(solution.x, current, strict=True)) == (
        pytest.approx(0.2, abs=1e-12)
    )
    assert "turnover" in solution.active


_DETERMINISM_SCRIPT = """
import random
from tests.unit.portfolio_optimizer.test_factor_quadratic import _construction_program, solve
for seed in (3, 11, 197, 329):
    solution = solve(_construction_program(seed))
    print(seed, solution.status.name, repr(solution.x), repr(solution.objective))
"""


def test_the_same_program_gives_the_same_bits_in_any_process() -> None:
    outputs = []
    for hash_seed in ("0", "12345"):
        environment = {**os.environ, "PYTHONHASHSEED": hash_seed}
        outputs.append(
            subprocess.run(
                [sys.executable, "-c", _DETERMINISM_SCRIPT],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
    assert outputs[0] == outputs[1]
    assert outputs[0].count("OPTIMAL") + outputs[0].count("NUMERICAL_FAILURE") == 4
