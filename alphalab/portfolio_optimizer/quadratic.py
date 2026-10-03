"""A dual active-set solver for strictly convex quadratic programs.

Every v3.8 construction method that is a quadratic program -- minimum variance,
mean-variance, maximum diversification after its change of variables, robust
mean-variance for a fixed robustness weight, and each step of a volatility cap --
is solved here, once:

.. code-block:: text

    minimize    1/2 x' G x + a' x
    subject to  n_i' x  = b_i                    (equalities)
                n_i' x >= b_i                    (inequalities)
                sum_k |x_k - c_k| <= L           (absolute-sum limits)

with ``G`` symmetric positive definite.

The method
----------

Goldfarb and Idnani's dual active-set method (*Mathematical Programming* 27,
1983) -- the algorithm behind the widely used ``quadprog``. It starts from the
unconstrained minimum ``x = -G^-1 a``, which is optimal for the dual, and adds
violated constraints one at a time, dropping an active one whenever its
multiplier would turn negative. It keeps two factors current by Givens
rotations: ``J``, with ``J' G J = I``, and the upper-triangular ``R`` with
``J' N = [R; 0]`` for the active normals ``N``. Each iteration therefore costs
``O(n^2)`` and nothing is ever re-factorized.

It was chosen over an interior-point or splitting method for three reasons that
matter here more than speed:

* **It terminates.** For a strictly convex problem it reaches the exact optimum
  in finitely many steps (in exact arithmetic), rather than approaching it to a
  tolerance -- so "optimal" means the KKT conditions hold, and the certificate
  below checks that they do.
* **It proves infeasibility.** When a violated constraint can be neither
  satisfied by a primal step nor accommodated by a dual one, the constraints are
  inconsistent, and the active set at that moment is the witness. The result
  names it, so a caller learns *which* limits conflict rather than that
  "something" is infeasible.
* **It is deterministic.** No random start, no step-size heuristic, no
  iteration-count-dependent result: the same program gives the same floats.

Absolute-sum limits without extra variables
--------------------------------------------

A turnover limit ``sum_k |x_k - w0_k| <= L`` and a gross-exposure limit
``sum_k |x_k| <= G`` are not linear. The textbook reformulation adds ``2n``
auxiliary variables with zero curvature, which would make ``G`` singular and
this method inapplicable. But each such limit is *exactly* the intersection of
the ``2^n`` linear constraints ``sum_k s_k (x_k - c_k) <= L`` over sign vectors
``s``, and a dual active-set method only ever needs the *most violated*
constraint -- which, for this family, is the one whose signs are
``s_k = sign(x_k - c_k)``. So the family is generated lazily: whenever the limit
is exceeded, the one facet it is exceeded along joins the candidates. The
feasible set is exactly the original one, and no variable is added.

Tolerances, stated
------------------

``feasibility_tolerance``
    A constraint is satisfied when its residual, in its own units, is no worse
    than this: an equality within it of its bound, an inequality no more than
    it short, an absolute sum no more than it over its limit.
``convergence_tolerance``
    The accepted KKT stationarity residual, ``||G x + a - N u||_inf``, relative
    to the size of the terms it balances. Checked *after* the method stops.

Two further thresholds are derived from machine epsilon and the problem size
rather than chosen: the Cholesky pivot floor ``n * eps * max(diag G)`` below
which ``G`` is refused as numerically singular, and the linear-dependence test
``||d2|| <= 10 n^2 eps ||d||`` on a candidate normal, which is ten times the
worst-case rounding error of the ``n``-term dot products that produce ``d``.

Status semantics
----------------

``OPTIMAL`` only when the method stopped with no violated constraint **and** the
KKT certificate verifies within both tolerances. ``INFEASIBLE`` when the method
proved inconsistency. ``ITERATION_LIMIT`` when it did not finish within
``max_iterations`` add-or-drop steps. ``NUMERICAL_FAILURE`` when ``G`` could not
be factorized, or the method stopped but its certificate does not verify --
which on a well-posed problem means the data were too ill-conditioned for
double precision. In every status but ``OPTIMAL`` no ``x`` is returned: a point
the method did not certify is not offered as a solution.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from itertools import pairwise
from types import MappingProxyType
from typing import Final, Protocol

from alphalab.portfolio_optimizer.exceptions import OptimizationError

__all__ = [
    "AbsoluteSumLimit",
    "Constrained",
    "LinearConstraint",
    "QuadraticProgram",
    "QuadraticSolution",
    "SolveStatus",
    "constraint_residuals",
    "solve_quadratic_program",
]

_EPSILON: Final = sys.float_info.epsilon

#: Safety factor on the worst-case rounding error of an ``n``-term dot product.
_DEPENDENCE_FACTOR: Final = 10.0


# --------------------------------------------------------------------------- #
# The program
# --------------------------------------------------------------------------- #


def _finite(value: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OptimizationError(f"{what} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise OptimizationError(f"{what} is {number!r}; every input must be finite.")
    return number


@dataclass(frozen=True, slots=True)
class LinearConstraint:
    """``sum_k terms_k * x_k (= | >=) bound``, stored sparsely.

    Attributes:
        label: What the constraint means to a reader -- it is how an optimal
            result names what binds and how an infeasible one names what
            conflicts. Required and non-blank.
        terms: ``(index, coefficient)`` pairs, indices strictly increasing and
            coefficients finite and non-zero. A constraint with no terms has no
            normal and is refused.
        bound: The right-hand side.
        equality: ``True`` for ``=``, ``False`` for ``>=``. An upper bound is
            written as its negation.
    """

    label: str
    terms: tuple[tuple[int, float], ...]
    bound: float
    equality: bool

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise OptimizationError("A constraint must be labelled.")
        if not self.terms:
            raise OptimizationError(f"Constraint {self.label!r} has no terms, so it has no normal.")
        previous = -1
        for index, coefficient in self.terms:
            if isinstance(index, bool) or not isinstance(index, int) or index <= previous:
                raise OptimizationError(
                    f"Constraint {self.label!r} must list strictly increasing integer indices."
                )
            previous = index
            if _finite(coefficient, f"coefficient in {self.label!r}") == 0.0:
                raise OptimizationError(f"Constraint {self.label!r} lists a zero coefficient.")
        _finite(self.bound, f"bound of {self.label!r}")


@dataclass(frozen=True, slots=True)
class AbsoluteSumLimit:
    """``sum_k |x[indices_k] - centers_k| <= limit``: turnover, gross exposure.

    Attributes:
        label: As for :class:`LinearConstraint`.
        indices: The variables summed, strictly increasing.
        centers: One per index: the point distance is measured from (current
            weights for turnover, zero for gross exposure).
        limit: The largest total distance allowed. Non-negative.
    """

    label: str
    indices: tuple[int, ...]
    centers: tuple[float, ...]
    limit: float

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise OptimizationError("An absolute-sum limit must be labelled.")
        if not self.indices or len(self.indices) != len(self.centers):
            raise OptimizationError(
                f"Absolute-sum limit {self.label!r} needs one center per index, and at least one."
            )
        if any(later <= earlier for earlier, later in pairwise(self.indices)):
            raise OptimizationError(
                f"Absolute-sum limit {self.label!r} must list strictly increasing indices."
            )
        for center in self.centers:
            _finite(center, f"center in {self.label!r}")
        if _finite(self.limit, f"limit of {self.label!r}") < 0.0:
            raise OptimizationError(f"Absolute-sum limit {self.label!r} is negative.")


@dataclass(frozen=True, slots=True)
class QuadraticProgram:
    """``minimize 1/2 x'Gx + a'x`` subject to linear constraints and absolute-sum limits.

    Attributes:
        hessian: ``G``, symmetric positive definite, row by row.
        linear: ``a``.
        constraints: Linear equalities and inequalities.
        absolute_sums: Absolute-sum limits.
    """

    hessian: tuple[tuple[float, ...], ...]
    linear: tuple[float, ...]
    constraints: tuple[LinearConstraint, ...] = ()
    absolute_sums: tuple[AbsoluteSumLimit, ...] = ()

    def __post_init__(self) -> None:
        size = len(self.linear)
        if size == 0:
            raise OptimizationError("A quadratic program needs at least one variable.")
        if len(self.hessian) != size or any(len(row) != size for row in self.hessian):
            raise OptimizationError(
                f"The Hessian must be {size} x {size} to match {size} linear coefficients."
            )
        for row in range(size):
            _finite(self.linear[row], f"linear coefficient {row}")
            for column in range(size):
                value = _finite(self.hessian[row][column], f"Hessian[{row}][{column}]")
                if value != self.hessian[column][row]:
                    raise OptimizationError(f"The Hessian is not symmetric at ({row}, {column}).")
        for constraint in self.constraints:
            if constraint.terms[-1][0] >= size:
                raise OptimizationError(
                    f"Constraint {constraint.label!r} reaches index {constraint.terms[-1][0]} of "
                    f"a {size}-variable program."
                )
        for family in self.absolute_sums:
            if family.indices[-1] >= size:
                raise OptimizationError(
                    f"Absolute-sum limit {family.label!r} reaches index {family.indices[-1]} of "
                    f"a {size}-variable program."
                )


class SolveStatus(Enum):
    """How a solve ended. See the module docstring for exact semantics."""

    OPTIMAL = auto()
    INFEASIBLE = auto()
    ITERATION_LIMIT = auto()
    NUMERICAL_FAILURE = auto()


@dataclass(frozen=True, slots=True)
class QuadraticSolution:
    """What the solver established, and the evidence for it.

    Attributes:
        status: How the solve ended.
        x: The certified solution, or ``None`` for any status but ``OPTIMAL``.
        objective: ``1/2 x'Gx + a'x`` at ``x``, or ``None``.
        iterations: Add-or-drop steps taken.
        active: Labels of the constraints active at the solution, in the order
            they became active.
        multipliers: Label -> Lagrange multiplier (summed over the facets of an
            absolute-sum limit). Non-negative for an inequality.
        conflict: For ``INFEASIBLE``, the labels of the violated constraint and
            of the active constraints that jointly exclude it. Empty otherwise.
        max_violation: The largest constraint residual at the point the solver
            stopped (``0`` when it stopped before reaching one).
        stationarity: The relative KKT stationarity residual at that point.
        pivot_ratio: Smallest over largest Cholesky pivot of ``G``: a
            conditioning indicator.
        detail: A sentence saying why the status is what it is.
    """

    status: SolveStatus
    x: tuple[float, ...] | None
    objective: float | None
    iterations: int
    active: tuple[str, ...]
    multipliers: Mapping[str, float]
    conflict: tuple[str, ...]
    max_violation: float
    stationarity: float
    pivot_ratio: float
    detail: str


# --------------------------------------------------------------------------- #
# Residuals -- shared by the solver's certificate and by callers checking a point
# --------------------------------------------------------------------------- #


def _dot(terms: Sequence[tuple[int, float]], x: Sequence[float]) -> float:
    return math.fsum(coefficient * x[index] for index, coefficient in terms)


def _absolute_sum(family: AbsoluteSumLimit, x: Sequence[float]) -> float:
    return math.fsum(
        abs(x[index] - center) for index, center in zip(family.indices, family.centers, strict=True)
    )


class Constrained(Protocol):
    """Anything that carries a program's constraints: what :func:`constraint_residuals` reads."""

    @property
    def constraints(self) -> tuple[LinearConstraint, ...]: ...

    @property
    def absolute_sums(self) -> tuple[AbsoluteSumLimit, ...]: ...


def constraint_residuals(program: Constrained, x: Sequence[float]) -> Mapping[str, float]:
    """Label -> how far ``x`` violates each constraint, in its own units; ``0`` when satisfied.

    The single definition of "satisfies a constraint" in this package: both
    solvers certify their answers with it, and risk parity -- whose solution is
    unique and fixed before any constraint is read -- checks its point with it.
    It reads only the constraints, so checking a point costs nothing ``n x n``.
    """

    residuals: dict[str, float] = {}
    for constraint in program.constraints:
        slack = _dot(constraint.terms, x) - constraint.bound
        violation = abs(slack) if constraint.equality else max(0.0, -slack)
        residuals[constraint.label] = max(residuals.get(constraint.label, 0.0), violation)
    for family in program.absolute_sums:
        violation = max(0.0, _absolute_sum(family, x) - family.limit)
        residuals[family.label] = max(residuals.get(family.label, 0.0), violation)
    return MappingProxyType(residuals)


# --------------------------------------------------------------------------- #
# The solver
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Active:
    label: str
    terms: tuple[tuple[int, float], ...]  # oriented so the constraint reads terms.x >= bound
    bound: float
    equality: bool
    origin: int | None = None  # index into QuadraticProgram.constraints, for a linear constraint
    facet: tuple[int, tuple[int, ...]] | None = None  # (family index, signs) for a facet
    flipped: bool = (
        False  # an equality negated to read as violated; its multiplier is reported negated
    )


@dataclass(slots=True)
class _State:
    size: int
    x: list[float]
    columns: list[list[float]]  # J, column-major
    r_columns: list[list[float]] = field(default_factory=list)  # R, column j has rows 0..j
    active: list[_Active] = field(default_factory=list)
    duals: list[float] = field(default_factory=list)
    iterations: int = 0


def _cholesky(hessian: Sequence[Sequence[float]]) -> tuple[list[list[float]], float] | None:
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


def _cholesky_solve(lower: Sequence[Sequence[float]], rhs: Sequence[float]) -> list[float]:
    """``x`` with ``L L' x = rhs``: forward then back substitution on a Cholesky factor.

    The one triangular solve in this package, used by
    :mod:`~alphalab.portfolio_optimizer.black_litterman` on the factor
    :func:`_cholesky` produces -- so the package holds one positive-definite
    factorization rather than two.
    """

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


def _initial_columns(lower: list[list[float]]) -> list[list[float]]:
    """``J = L^-T`` column by column: column ``j`` of ``J`` is row ``j`` of ``L^-1``."""

    size = len(lower)
    inverse = [[0.0] * size for _ in range(size)]
    for column in range(size):
        for row in range(column, size):
            total = (1.0 if row == column else 0.0) - math.fsum(
                lower[row][k] * inverse[k][column] for k in range(column, row)
            )
            inverse[row][column] = total / lower[row][row]
    return [list(inverse[row]) for row in range(size)]


def _project(state: _State, terms: Sequence[tuple[int, float]]) -> list[float]:
    """``d = J' n`` for a sparse ``n``."""

    return [
        math.fsum(coefficient * column[index] for index, coefficient in terms)
        for column in state.columns
    ]


def _rotate(first: list[float], second: list[float], cosine: float, sine: float) -> None:
    for index in range(len(first)):
        a = first[index]
        b = second[index]
        first[index] = cosine * a + sine * b
        second[index] = -sine * a + cosine * b


def _givens(a: float, b: float) -> tuple[float, float, float]:
    """``(c, s, h)`` with ``c*a + s*b = h`` and ``-s*a + c*b = 0``.

    ``sqrt(a*a + b*b)`` rather than ``math.hypot``: two correctly rounded
    operations and a correctly rounded square root give the same bits on every
    IEEE-754 platform, which is what a reproducible optimization needs.
    """

    height = math.sqrt(a * a + b * b)
    if height == 0.0:
        return 1.0, 0.0, 0.0
    return a / height, b / height, height


def _add(state: _State, projected: list[float], constraint: _Active, dual: float) -> None:
    """Make ``constraint`` active, updating ``J`` and ``R`` by Givens rotations."""

    count = len(state.active)
    for index in range(state.size - 1, count, -1):
        if projected[index] == 0.0:
            continue
        cosine, sine, height = _givens(projected[index - 1], projected[index])
        projected[index - 1] = height
        projected[index] = 0.0
        _rotate(state.columns[index - 1], state.columns[index], cosine, sine)
    state.r_columns.append(projected[: count + 1])
    state.active.append(constraint)
    state.duals.append(dual)


def _drop(state: _State, position: int) -> None:
    """Remove the active constraint at ``position``, restoring ``R`` to triangular form."""

    del state.active[position]
    del state.duals[position]
    del state.r_columns[position]
    remaining = len(state.active)
    for column in range(position, remaining):
        entries = state.r_columns[column]
        cosine, sine, height = _givens(entries[column], entries[column + 1])
        entries[column] = height
        entries.pop()
        for later in range(column + 1, remaining):
            other = state.r_columns[later]
            a = other[column]
            b = other[column + 1]
            other[column] = cosine * a + sine * b
            other[column + 1] = -sine * a + cosine * b
        _rotate(state.columns[column], state.columns[column + 1], cosine, sine)


def _back_substitute(state: _State, projected: Sequence[float]) -> list[float]:
    """``r`` with ``R r = d[:q]``."""

    count = len(state.active)
    result = [0.0] * count
    for row in range(count - 1, -1, -1):
        total = projected[row] - math.fsum(
            state.r_columns[later][row] * result[later] for later in range(row + 1, count)
        )
        result[row] = total / state.r_columns[row][row]
    return result


def _slack(constraint: _Active, x: Sequence[float]) -> float:
    return _dot(constraint.terms, x) - constraint.bound


def _norm(terms: Sequence[tuple[int, float]]) -> float:
    return math.sqrt(math.fsum(coefficient * coefficient for _, coefficient in terms))


def _oriented(origin: int, constraint: LinearConstraint, x: Sequence[float]) -> _Active:
    """An equality, flipped if need be so that it reads ``terms.x >= bound`` and is violated."""

    slack = _dot(constraint.terms, x) - constraint.bound
    if constraint.equality and slack > 0.0:
        return _Active(
            constraint.label,
            tuple((index, -coefficient) for index, coefficient in constraint.terms),
            -constraint.bound,
            True,
            origin=origin,
            flipped=True,
        )
    return _Active(
        constraint.label, constraint.terms, constraint.bound, constraint.equality, origin=origin
    )


def _facet(family_index: int, family: AbsoluteSumLimit, x: Sequence[float]) -> _Active:
    """The facet of an absolute-sum limit that ``x`` violates it along.

    ``sum_k s_k (x_k - c_k) <= L`` with ``s_k = sign(x_k - c_k)`` (``+1`` at a
    tie), written as ``-sum_k s_k x_k >= -L - sum_k s_k c_k``.
    """

    signs = tuple(
        1 if x[index] - center >= 0.0 else -1
        for index, center in zip(family.indices, family.centers, strict=True)
    )
    terms = tuple((index, -float(sign)) for index, sign in zip(family.indices, signs, strict=True))
    bound = -family.limit - math.fsum(
        sign * center for sign, center in zip(signs, family.centers, strict=True)
    )
    return _Active(family.label, terms, bound, False, facet=(family_index, signs))


def _most_violated(
    program: QuadraticProgram, state: _State, norms: Sequence[float], tolerance: float
) -> _Active | None:
    """The inequality or absolute-sum facet violated most, per unit of normal; or ``None``.

    Violation is measured in the constraint's own units against the
    feasibility tolerance, and ranked by distance -- violation over the
    normal's length -- so that a constraint is not preferred merely for having
    large coefficients. Ties go to the earlier declaration.
    """

    active_origins = {item.origin for item in state.active if item.origin is not None}
    active_facets = {item.facet for item in state.active if item.facet is not None}
    best: _Active | None = None
    best_distance = 0.0
    for origin, constraint in enumerate(program.constraints):
        if constraint.equality or origin in active_origins:
            continue
        slack = _dot(constraint.terms, state.x) - constraint.bound
        if slack >= -tolerance:
            continue
        distance = slack / norms[origin]
        if best is None or distance < best_distance:
            best, best_distance = _oriented(origin, constraint, state.x), distance
    for index, family in enumerate(program.absolute_sums):
        excess = _absolute_sum(family, state.x) - family.limit
        if excess <= tolerance:
            continue
        facet = _facet(index, family, state.x)
        if facet.facet in active_facets:
            continue
        distance = -excess / _norm(facet.terms)
        if best is None or distance < best_distance:
            best, best_distance = facet, distance
    return best


def _dependent(projected: Sequence[float], count: int, size: int) -> tuple[bool, float]:
    """Whether the candidate normal lies in the span of the active normals.

    Returns the decision and ``||d2||^2``, which is ``z' n`` for the primal step.
    """

    tail = math.fsum(value * value for value in projected[count:])
    whole = math.fsum(value * value for value in projected)
    threshold = _DEPENDENCE_FACTOR * size * size * _EPSILON
    return tail <= threshold * threshold * whole, tail


def _step_direction(state: _State, projected: Sequence[float]) -> list[float]:
    """``z = J2 d2``: the primal direction that moves only the candidate's slack."""

    count = len(state.active)
    direction = [0.0] * state.size
    for column in range(count, state.size):
        weight = projected[column]
        if weight == 0.0:
            continue
        values = state.columns[column]
        for index in range(state.size):
            direction[index] += weight * values[index]
    return direction


class _Infeasible(Exception):
    def __init__(self, conflict: tuple[str, ...]) -> None:
        super().__init__("infeasible")
        self.conflict = conflict


class _IterationLimit(Exception):
    pass


def _satisfy(state: _State, candidate: _Active, max_iterations: int, tolerance: float) -> bool:
    """Run Goldfarb-Idnani steps until ``candidate`` is active; ``False`` if it was redundant.

    Raises ``_Infeasible`` when the candidate can be satisfied by neither a
    primal nor a dual step, and ``_IterationLimit`` when the step budget runs
    out.
    """

    dual = 0.0
    while True:
        if state.iterations >= max_iterations:
            raise _IterationLimit
        projected = _project(state, candidate.terms)
        count = len(state.active)
        dependent, curvature = _dependent(projected, count, state.size)
        slack = _slack(candidate, state.x)
        if dependent and candidate.equality and abs(slack) <= tolerance:
            # A consistent equality implied by those already active: nothing to do.
            return False
        r = _back_substitute(state, projected[:count]) if count else []

        partial = math.inf
        blocking = -1
        for position in range(count):
            if state.active[position].equality or r[position] <= 0.0:
                continue
            ratio = state.duals[position] / r[position]
            if ratio < partial:
                partial, blocking = ratio, position

        # Clamped at zero: in exact arithmetic the candidate stays violated until
        # its full step, and a rounding-sized positive slack must not turn that
        # step backwards.
        full = math.inf if dependent else max(0.0, -slack / curvature)
        step = min(partial, full)
        state.iterations += 1

        if step == math.inf:
            involved = tuple(
                state.active[position].label
                for position in range(count)
                if r and r[position] != 0.0
            )
            raise _Infeasible((candidate.label, *dict.fromkeys(involved)))

        if full != math.inf:
            direction = _step_direction(state, projected)
            for index in range(state.size):
                state.x[index] += step * direction[index]
        for position in range(count):
            state.duals[position] -= step * r[position]
        dual += step

        if full <= partial:
            _add(state, projected, candidate, dual)
            return True
        _drop(state, blocking)


def _certificate(program: QuadraticProgram, state: _State) -> tuple[float, float, dict[str, float]]:
    """``(max violation, relative stationarity, multipliers by label)`` at the current point."""

    size = state.size
    gradient = [
        math.fsum(program.hessian[row][column] * state.x[column] for column in range(size))
        for row in range(size)
    ]
    curvature_scale = max((abs(value) for value in gradient), default=0.0)
    residual = [gradient[index] + program.linear[index] for index in range(size)]
    multipliers: dict[str, float] = {}
    constraint_scale = 0.0
    for item, dual in zip(state.active, state.duals, strict=True):
        for index, coefficient in item.terms:
            residual[index] -= dual * coefficient
            constraint_scale = max(constraint_scale, abs(dual * coefficient))
        reported = -dual if item.flipped else dual
        multipliers[item.label] = multipliers.get(item.label, 0.0) + reported
    scale = max(
        1.0,
        curvature_scale,
        max(abs(value) for value in program.linear),
        constraint_scale,
    )
    stationarity = max(abs(value) for value in residual) / scale
    residuals = constraint_residuals(program, state.x)
    violation = max(residuals.values(), default=0.0)
    return violation, stationarity, multipliers


def solve_quadratic_program(
    program: QuadraticProgram,
    *,
    feasibility_tolerance: float,
    convergence_tolerance: float,
    max_iterations: int,
) -> QuadraticSolution:
    """Solve ``program`` and certify the answer. See the module docstring.

    Raises:
        OptimizationError: If a tolerance is not a positive finite number below
            one, or ``max_iterations`` is not a positive integer. An infeasible
            or unsolvable *program* is a status, not an exception.
    """

    for name, value in (
        ("feasibility_tolerance", feasibility_tolerance),
        ("convergence_tolerance", convergence_tolerance),
    ):
        if not 0.0 < _finite(value, name) < 1.0:
            raise OptimizationError(f"{name} is {value!r}; it must lie in (0, 1).")
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations < 1
    ):
        raise OptimizationError(
            f"max_iterations is {max_iterations!r}; it must be a positive integer."
        )

    factored = _cholesky(program.hessian)
    if factored is None:
        return QuadraticSolution(
            SolveStatus.NUMERICAL_FAILURE,
            None,
            None,
            0,
            (),
            MappingProxyType({}),
            (),
            0.0,
            0.0,
            0.0,
            "The Hessian is not numerically positive definite: a Cholesky pivot fell to the "
            "floor n * eps * max(diag). The program has no unique minimum in double precision.",
        )
    lower, pivot_ratio = factored
    size = len(program.linear)
    columns = _initial_columns(lower)
    projected_linear = [
        math.fsum(column[index] * program.linear[index] for index in range(size))
        for column in columns
    ]
    x = [
        -math.fsum(columns[j][index] * projected_linear[j] for j in range(size))
        for index in range(size)
    ]
    state = _State(size=size, x=x, columns=columns)
    norms = [_norm(constraint.terms) for constraint in program.constraints]

    def unfinished(
        status: SolveStatus, conflict: tuple[str, ...], detail: str
    ) -> QuadraticSolution:
        violation, stationarity, _ = _certificate(program, state)
        return QuadraticSolution(
            status,
            None,
            None,
            state.iterations,
            tuple(item.label for item in state.active),
            MappingProxyType({}),
            conflict,
            violation,
            stationarity,
            pivot_ratio,
            detail,
        )

    try:
        for origin, constraint in enumerate(program.constraints):
            if constraint.equality:
                _satisfy(
                    state,
                    _oriented(origin, constraint, state.x),
                    max_iterations,
                    feasibility_tolerance,
                )
        while True:
            candidate = _most_violated(program, state, norms, feasibility_tolerance)
            if candidate is None:
                break
            _satisfy(state, candidate, max_iterations, feasibility_tolerance)
    except _Infeasible as infeasible:
        return unfinished(
            SolveStatus.INFEASIBLE,
            infeasible.conflict,
            "The constraints are inconsistent: the first named constraint cannot be satisfied "
            "while the others named hold.",
        )
    except _IterationLimit:
        return unfinished(
            SolveStatus.ITERATION_LIMIT,
            (),
            f"The method did not finish within {max_iterations} add-or-drop steps.",
        )

    violation, stationarity, multipliers = _certificate(program, state)
    if violation > feasibility_tolerance or stationarity > convergence_tolerance:
        return QuadraticSolution(
            SolveStatus.NUMERICAL_FAILURE,
            None,
            None,
            state.iterations,
            tuple(item.label for item in state.active),
            MappingProxyType({}),
            (),
            violation,
            stationarity,
            pivot_ratio,
            f"The method stopped, but its KKT certificate does not verify: largest violation "
            f"{violation:.3g} against {feasibility_tolerance:.3g}, stationarity "
            f"{stationarity:.3g} against {convergence_tolerance:.3g}. The data are too "
            "ill-conditioned for double precision at these tolerances.",
        )
    solution = tuple(state.x)
    objective = 0.5 * math.fsum(
        solution[row] * program.hessian[row][column] * solution[column]
        for row in range(size)
        for column in range(size)
    ) + math.fsum(program.linear[index] * solution[index] for index in range(size))
    return QuadraticSolution(
        SolveStatus.OPTIMAL,
        solution,
        objective,
        state.iterations,
        tuple(item.label for item in state.active),
        MappingProxyType(dict(sorted(multipliers.items()))),
        (),
        violation,
        stationarity,
        pivot_ratio,
        "Optimal: no constraint is violated and the KKT certificate verifies.",
    )
