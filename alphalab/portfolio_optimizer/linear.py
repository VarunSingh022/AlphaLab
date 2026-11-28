"""A certified linear program: construction's third method (v4.3, ADR-0063).

.. code-block:: text

    minimize    c' x
    subject to  each LinearConstraint:  sum_k a_k x_k  (= | >=)  bound
                lower_j <= x_j <= upper_j,  every bound finite

Every variable has finite bounds, so the program is never unbounded: it has an
optimum or is infeasible. Construction gives every variable it introduces a
finite range implied by the problem (ADR-0063 section 3).

The method
----------

1. **The bounded standard form.** Each inequality gains a slack in ``[0, s]``,
   ``s`` the most the row can exceed its bound over the box, and every variable
   is shifted to ``[0, u]``. A fixed variable leaves the system, and a row left
   with no variable holds or fails by its bound alone: it is decided there, and
   a program left with no row is solved exactly, each variable at the bound its
   cost prefers.
2. **Mehrotra's predictor-corrector interior point** (Mehrotra, *SIAM Journal on
   Optimization* 2, 1992; Wright, *Primal-Dual Interior-Point Methods*, 1997).
   Each Newton system is reduced to the normal equations ``A T A' dy = r``, ``T``
   diagonal and positive, built column by column from the rows' sparse terms and
   factored by :func:`alphalab.common.linalg.cholesky`. The normal matrix is
   first scaled to a unit diagonal (Jacobi's scaling), each row by its own
   diagonal entry or, when that is below ``1e-12`` of the largest, by that, and
   ``1e-12`` is added to the scaled diagonal: dependent rows stay factorable, a
   row is lifted by ``1e-12`` of its own entry -- by ``1e-24`` of the largest
   when its own is that small -- and each direction is then refined against
   the unraised matrix. A direction that leaves the floating-point range ends
   the interior point, as a matrix that cannot be factored does. The
   certificate, not the regularization, decides the answer.
3. **An exact finish.** From the converged iterate each variable is read as at
   its lower bound, at its upper bound or basic. The basis is the basic
   variables when they are as many as the rows and independent; otherwise -- a
   degenerate vertex, or an optimal face -- the first independent columns, the
   basic ones before the rest. Its system is solved directly -- Gaussian
   elimination with partial pivoting -- for a vertex, and the row multipliers
   from the transposed system; a basic variable outside its range is moved to
   the bound it crossed and the basis chosen again, a bounded number of times.
4. **The certificate,** checked on the vertex -- with its own multipliers, then
   with the iterate's -- or, when neither verifies, on the interior iterate: the
   point within ``feasibility_tolerance`` of every row and bound, and its
   objective within ``convergence_tolerance`` (relative) of a proven lower
   bound. For multipliers ``y`` of the rows, ``d = c - A' y``, the
   bound is ``b' y + sum_j min(d_j lower_j, d_j upper_j)`` -- valid for every
   ``y`` by weak duality, so nothing else need be trusted.

``INFEASIBLE`` only with a verified Farkas certificate. When the interior point
does not reach a certified answer -- it fails to converge, or its primal
residual stops halving for twenty iterations, as an infeasible program's does
-- a Phase I program, the least total violation of the rows over the box, is
solved the same way. Its multipliers
``y`` prove infeasibility when ``b' y`` exceeds the most ``y' A x`` can be over
the box by more than ``feasibility_tolerance * sum |y_i|``: then no point of the
box is within the tolerance of every row. The rows they weight are named as the
conflict. An interior point that only stalled, and that Phase I does not prove
infeasible, resumes from where it stopped within the budget left. Otherwise
``ITERATION_LIMIT`` or ``NUMERICAL_FAILURE``, with no ``x``.

Every tolerance on a row is in the row's own units, as construction's are: a
point is feasible when no row misses its bound by more than
``feasibility_tolerance``.

Only ``+ - x /`` and ``sqrt`` are used, each correctly rounded under IEEE-754,
in a fixed order, so the same program gives the same bits on every host.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.linalg import CholeskyFactor, NotPositiveDefiniteError, cholesky
from alphalab.portfolio_optimizer.exceptions import OptimizationError
from alphalab.portfolio_optimizer.quadratic import LinearConstraint, SolveStatus

__all__ = [
    "LinearCertificate",
    "LinearProgram",
    "LinearSolution",
    "solve_linear_program",
]

#: The fraction of the step to the boundary an iterate takes.
_STEP: Final = 0.995

#: The normal matrix, scaled to a unit diagonal, has this added to its diagonal, so
#: that dependent rows stay factorable; each direction is then refined against the
#: unraised matrix, so the step meets the primal equations to rounding. Until v4.3's
#: M9 the lift was this fraction of the *largest* diagonal entry, which swamped a
#: row whose own entry was many orders smaller -- a CVaR program's excess rows near
#: the optimum -- so its direction missed the primal equations, the step put the
#: miss into a row already met, and the iterate jammed against its bounds.
_REGULARIZATION: Final = 1e-12

#: How many refinements a normal-equation solve may take.
_REFINEMENTS: Final = 4

#: How many times the vertex finish may correct the basis it read.
_FINISH_ATTEMPTS: Final = 3

#: Below this, an iterate's coordinate is no longer strictly inside its bounds.
_TINY: Final = 1e-280

#: An interior point whose primal residual has not halved in this many iterations
#: has stalled. An infeasible program's residual stops at its least violation
#: while its multipliers grow without bound; a feasible one's falls geometrically.
_STALL: Final = 20


def _finite(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OptimizationError(f"{what} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise OptimizationError(f"{what} is {number!r}; it must be finite.")
    return number


@dataclass(frozen=True, slots=True)
class LinearProgram:
    """``minimize c'x`` subject to linear constraints and a finite range for every variable.

    Attributes:
        costs: ``c``: one finite cost per variable.
        constraints: The rows, each an equality or a ``>=`` over the variables
            by index (:class:`~alphalab.portfolio_optimizer.quadratic.LinearConstraint`).
        lower: Each variable's lower bound: finite.
        upper: Each variable's upper bound: finite, and not below its lower.
    """

    costs: tuple[float, ...]
    constraints: tuple[LinearConstraint, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]

    def __post_init__(self) -> None:
        size = len(self.costs)
        if size == 0:
            raise OptimizationError("A linear program needs at least one variable.")
        if len(self.lower) != size or len(self.upper) != size:
            raise OptimizationError(
                f"A linear program of {size} variables needs {size} lower and upper bounds; "
                f"got {len(self.lower)} and {len(self.upper)}."
            )
        costs = tuple(_finite(value, f"cost[{index}]") for index, value in enumerate(self.costs))
        lower = tuple(_finite(value, f"lower[{index}]") for index, value in enumerate(self.lower))
        upper = tuple(_finite(value, f"upper[{index}]") for index, value in enumerate(self.upper))
        for index, (low, high) in enumerate(zip(lower, upper, strict=True)):
            if low > high:
                raise OptimizationError(
                    f"Variable {index}'s lower bound {low!r} is above its upper bound {high!r}."
                )
        for constraint in self.constraints:
            if not isinstance(constraint, LinearConstraint):
                raise OptimizationError(f"{constraint!r} is not a LinearConstraint.")
            if constraint.terms[-1][0] >= size:
                raise OptimizationError(
                    f"Constraint {constraint.label!r} names variable {constraint.terms[-1][0]}; "
                    f"the program has {size}."
                )
        object.__setattr__(self, "costs", costs)
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)
        object.__setattr__(self, "constraints", tuple(self.constraints))


class LinearCertificate(Enum):
    """What established a :class:`LinearSolution`'s status."""

    #: A vertex, solved directly on the basis the interior point found.
    VERTEX = "vertex"
    #: The interior iterate itself, within the tolerances of a proven bound.
    INTERIOR = "interior"
    #: Multipliers proving that no point satisfies every row within the box.
    FARKAS = "farkas"


@dataclass(frozen=True, slots=True)
class LinearSolution:
    """What the solver established, and the evidence for it.

    Attributes:
        status: How the solve ended.
        x: The certified solution, or ``None`` for any status but ``OPTIMAL``.
        objective: ``c'x`` at ``x``, or ``None``.
        bound: The proven lower bound on every feasible objective that
            certifies ``x``, or ``None``.
        multipliers: One multiplier per constraint of the program, in its
            order: for ``OPTIMAL`` the rows' multipliers that prove the bound,
            for ``INFEASIBLE`` the Farkas certificate's. Empty without a
            certificate. By position, since labels need not be unique.
        conflict: For ``INFEASIBLE``, the labels of the rows the Farkas
            certificate weights, each once, in the program's order. Empty
            otherwise.
        certificate: How the status was established, or ``None``.
        iterations: Interior-point iterations, Phase I's included.
        max_violation: The largest row or bound residual at the point.
        gap: The relative gap between ``objective`` and ``bound``, or for
            ``INFEASIBLE`` the margin by which the rows cannot hold.
        detail: A sentence saying why the status is what it is.
    """

    status: SolveStatus
    x: tuple[float, ...] | None
    objective: float | None
    bound: float | None
    multipliers: tuple[float, ...]
    conflict: tuple[str, ...]
    certificate: LinearCertificate | None
    iterations: int
    max_violation: float
    gap: float
    detail: str


# --------------------------------------------------------------------------- #
# The bounded standard form: rows A x = b, 0 <= x <= u
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Standard:
    rows: tuple[tuple[tuple[int, float], ...], ...]
    columns: tuple[tuple[tuple[int, float], ...], ...]
    rhs: tuple[float, ...]
    costs: tuple[float, ...]
    upper: tuple[float, ...]
    inequality: tuple[bool, ...]
    #: For each standard variable, the program's variable it is, or -1 for a slack.
    origin: tuple[int, ...]
    #: For each row, the program's constraint it is.
    source: tuple[int, ...]
    #: ``(constraint, by how much it misses, its Farkas weight)`` for each constraint
    #: every variable of which is fixed: the weight is the sign making its miss positive.
    vacuous: tuple[tuple[int, float, float], ...]


def _standard(
    program: LinearProgram, costs: Sequence[float] | None = None, phase_one: bool = False
) -> tuple[_Standard, list[float]]:
    """The program shifted to ``[0, u]``, slacks added, fixed variables folded in.

    Returns the form and, for each program variable, its fixed value or ``nan``
    when it is free to move.
    """

    lower, upper = program.lower, program.upper
    width = len(program.costs)
    fixed = [lower[j] if upper[j] == lower[j] else math.nan for j in range(width)]
    position: dict[int, int] = {}
    origin: list[int] = []
    bounds: list[float] = []
    objective: list[float] = []
    base = program.costs if costs is None else costs
    for j in range(width):
        if math.isnan(fixed[j]):
            position[j] = len(origin)
            origin.append(j)
            bounds.append(upper[j] - lower[j])
            objective.append(0.0 if phase_one else base[j])
    rows: list[list[tuple[int, float]]] = []
    rhs: list[float] = []
    inequality: list[bool] = []
    source: list[int] = []
    vacuous: list[tuple[int, float, float]] = []
    for number, constraint in enumerate(program.constraints):
        shift = math.fsum(
            a * (lower[k] if math.isnan(fixed[k]) else fixed[k]) for k, a in constraint.terms
        )
        row = [(position[k], a) for k, a in constraint.terms if k in position]
        target = constraint.bound - shift
        if not row:
            # Every variable of the row is fixed: it holds, or misses by this much,
            # whatever is solved, so it leaves the system.
            if constraint.equality:
                vacuous.append((number, abs(target), -1.0 if target < 0.0 else 1.0))
            else:
                vacuous.append((number, max(target, 0.0), 1.0))
            continue
        if not constraint.equality:
            # sum a x' - s = target, the slack s in [0, most the row can exceed it].
            most = math.fsum(
                max(a, 0.0) * bounds[position[k]] for k, a in constraint.terms if k in position
            )
            slack = max(most - target, 0.0)
            if slack > 0.0:
                row.append((len(origin), -1.0))
                origin.append(-1)
                bounds.append(slack)
                objective.append(0.0)
        rows.append(row)
        rhs.append(target)
        inequality.append(not constraint.equality)
        source.append(number)
    if phase_one:
        # Artificials p - q on every row: the program's least total violation.
        for index, target in enumerate(rhs):
            reach = abs(target) + math.fsum(
                abs(a) * bounds[k] for k, a in rows[index] if origin[k] >= 0
            )
            for sign in (1.0, -1.0):
                rows[index].append((len(origin), sign))
                origin.append(-2)
                bounds.append(reach + 1.0)
                objective.append(1.0)
    columns: list[list[tuple[int, float]]] = [[] for _ in origin]
    for index, row in enumerate(rows):
        for k, a in row:
            columns[k].append((index, a))
    form = _Standard(
        rows=tuple(tuple(row) for row in rows),
        columns=tuple(tuple(column) for column in columns),
        rhs=tuple(rhs),
        costs=tuple(objective),
        upper=tuple(bounds),
        inequality=tuple(inequality),
        origin=tuple(origin),
        source=tuple(source),
        vacuous=tuple(vacuous),
    )
    return form, fixed


def _times(form: _Standard, x: Sequence[float]) -> list[float]:
    """``A x``."""

    return [math.fsum(a * x[k] for k, a in row) for row in form.rows]


def _transposed(form: _Standard, y: Sequence[float]) -> list[float]:
    """``A' y``."""

    return [math.fsum(a * y[i] for i, a in column) for column in form.columns]


def _largest(values: Sequence[float]) -> float:
    return max((abs(value) for value in values), default=0.0)


# --------------------------------------------------------------------------- #
# The interior point
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Iterate:
    x: list[float]
    w: list[float]
    y: list[float]
    z: list[float]
    v: list[float]


def _start(form: _Standard) -> _Iterate:
    """The box's centre, and dual slacks meeting the dual equations exactly at ``y = 0``."""

    x = [bound / 2.0 for bound in form.upper]
    w = [bound - value for bound, value in zip(form.upper, x, strict=True)]
    v = [max(-cost, 0.0) + 1.0 for cost in form.costs]
    z = [cost + slack for cost, slack in zip(form.costs, v, strict=True)]
    return _Iterate(x, w, [0.0] * len(form.rows), z, v)


def _boundary(values: Sequence[float], steps: Sequence[float]) -> float:
    """The largest ``alpha <= 1`` keeping ``values + alpha * steps`` non-negative."""

    alpha = 1.0
    for value, step in zip(values, steps, strict=True):
        if step < 0.0:
            alpha = min(alpha, -value / step)
    return alpha


class _Overflow(Exception):
    """A Newton direction left the floating-point range, so the iterate can go no further.

    An infeasible program's multipliers grow without bound, and under Jacobi's
    scaling nothing caps them; an interior point that meets this stops, as it
    does when its normal matrix cannot be factored, and Phase I decides.
    """


def _require_finite(values: Sequence[float]) -> None:
    if not all(math.isfinite(value) for value in values):
        raise _Overflow


@dataclass(frozen=True, slots=True)
class _NormalFactor:
    """``A T A'``, scaled to a unit diagonal by ``scale``, lifted, and factored.

    :meth:`solve` answers the unscaled system: ``A T A' dy = rhs`` is
    ``(S A T A' S)(S^-1 dy) = S rhs``, with ``S`` the diagonal of ``scale``.

    Raises:
        _Overflow: If the right-hand side or the answer is not finite.
    """

    factor: CholeskyFactor
    scale: tuple[float, ...]

    def solve(self, rhs: Sequence[float]) -> list[float]:
        scaled = [s * value for s, value in zip(self.scale, rhs, strict=True)]
        _require_finite(scaled)
        answer = [s * value for s, value in zip(self.scale, self.factor.solve(scaled), strict=True)]
        _require_finite(answer)
        return answer


def _normal_factor(form: _Standard, theta: Sequence[float]) -> _NormalFactor:
    size = len(form.rows)
    matrix = [[0.0] * size for _ in range(size)]
    for k, column in enumerate(form.columns):
        weight = theta[k]
        for first, (p, a) in enumerate(column):
            scaled = weight * a
            for q, b in column[: first + 1]:
                if p >= q:
                    matrix[p][q] += scaled * b
                else:
                    matrix[q][p] += scaled * b
    # Jacobi's scaling: each row and column by the inverse root of its diagonal
    # entry -- or of _REGULARIZATION of the largest (of one, when that is smaller),
    # when its own is below that -- then the lift. A row is lifted by
    # _REGULARIZATION of its own entry, and one far below the largest by
    # _REGULARIZATION squared of the largest: never swamped near an optimum, and an
    # infeasible program's multipliers still grow by a bounded step each iteration.
    floor = _REGULARIZATION * max((matrix[i][i] for i in range(size)), default=1.0)
    floor = max(floor, _REGULARIZATION)
    scale = [1.0 / math.sqrt(max(matrix[i][i], floor)) for i in range(size)]
    for i in range(size):
        row = matrix[i]
        for j in range(i):
            row[j] *= scale[i] * scale[j]
        row[i] = row[i] * (scale[i] * scale[i]) + _REGULARIZATION
    return _NormalFactor(cholesky(matrix), tuple(scale))


def _direction(
    form: _Standard,
    point: _Iterate,
    theta: Sequence[float],
    factor: _NormalFactor,
    r_p: Sequence[float],
    r_d: Sequence[float],
    r_xz: Sequence[float],
    r_wv: Sequence[float],
) -> tuple[list[float], list[float], list[float], list[float]]:
    """One Newton direction ``(dx, dy, dz, dv)``, by the normal equations."""

    shifted = [
        rd - rxz / x + rwv / w
        for rd, rxz, rwv, x, w in zip(r_d, r_xz, r_wv, point.x, point.w, strict=True)
    ]
    scaled = [t * value for t, value in zip(theta, shifted, strict=True)]
    _require_finite(scaled)
    rhs = [rp + value for rp, value in zip(r_p, _times(form, scaled), strict=True)]
    dy = list(factor.solve(rhs)) if rhs else []
    scale = 1.0 + _largest(rhs)
    for _ in range(_REFINEMENTS if rhs else 0):
        # The regularized factor's answer, corrected against A T A' itself.
        applied = _times(form, [t * a for t, a in zip(theta, _transposed(form, dy), strict=True)])
        residual = [r - a for r, a in zip(rhs, applied, strict=True)]
        if _largest(residual) <= 1e-15 * scale:
            break
        dy = [d + c for d, c in zip(dy, factor.solve(residual), strict=True)]
    aty = _transposed(form, dy)
    dx = [t * (value - s) for t, value, s in zip(theta, aty, shifted, strict=True)]
    dz = [(rxz - z * d) / x for rxz, z, d, x in zip(r_xz, point.z, dx, point.x, strict=True)]
    dv = [(rwv + v * d) / w for rwv, v, d, w in zip(r_wv, point.v, dx, point.w, strict=True)]
    for values in (dx, dz, dv):
        _require_finite(values)
    return dx, dy, dz, dv


def _interior_point(
    form: _Standard,
    tolerance: float,
    max_iterations: int,
    *,
    watch: bool = False,
    start: _Iterate | None = None,
) -> tuple[_Iterate, int, bool, bool]:
    """Mehrotra's predictor-corrector from the box's centre, or from ``start``.

    Returns the iterate, the iterations taken, whether it converged, and whether
    it stopped on a stall, which it does only when ``watch``ing for one. Each
    iteration depends on the iterate alone, so one resumed from where it
    stopped takes the steps it would have taken.
    """

    point = _start(form) if start is None else start
    count = 2 * len(form.costs)
    c_scale = 1.0 + _largest(form.costs)
    history: list[float] = []
    for iteration in range(max_iterations):
        ax = _times(form, point.x)
        aty = _transposed(form, point.y)
        r_p = [b - value for b, value in zip(form.rhs, ax, strict=True)]
        r_d = [
            c - value - z + v
            for c, value, z, v in zip(form.costs, aty, point.z, point.v, strict=True)
        ]
        complementarity = math.fsum(x * z for x, z in zip(point.x, point.z, strict=True))
        complementarity += math.fsum(w * v for w, v in zip(point.w, point.v, strict=True))
        mu = complementarity / count
        objective = math.fsum(c * x for c, x in zip(form.costs, point.x, strict=True))
        residual = _largest(r_p)
        if (
            residual <= tolerance
            and _largest(r_d) <= tolerance * c_scale
            and complementarity <= tolerance * (1.0 + abs(objective))
        ):
            return point, iteration, True, False
        if watch:
            history.append(residual)
            if (
                len(history) > _STALL
                and residual > tolerance
                and residual > 0.5 * history[-1 - _STALL]
            ):
                return point, iteration, False, True
        if min(point.x + point.w + point.z + point.v, default=1.0) <= _TINY:
            # An iterate pressed against a bound until it underflows makes no more
            # progress; an infeasible program does this. Phase I decides.
            return point, iteration, False, False
        theta = [
            1.0 / (z / x + v / w)
            for x, w, z, v in zip(point.x, point.w, point.z, point.v, strict=True)
        ]
        try:
            factor = _normal_factor(form, theta)
        except (NotPositiveDefiniteError, AlphaLabValidationError):
            return point, iteration, False, False
        # Predictor: the affine-scaling direction.
        r_xz = [-x * z for x, z in zip(point.x, point.z, strict=True)]
        r_wv = [-w * v for w, v in zip(point.w, point.v, strict=True)]
        try:
            dx, dy, dz, dv = _direction(form, point, theta, factor, r_p, r_d, r_xz, r_wv)
        except _Overflow:
            return point, iteration, False, False
        alpha_p = min(_boundary(point.x, dx), _boundary(point.w, [-d for d in dx]))
        alpha_d = min(_boundary(point.z, dz), _boundary(point.v, dv))
        affine = math.fsum(
            (x + alpha_p * a) * (z + alpha_d * b)
            for x, a, z, b in zip(point.x, dx, point.z, dz, strict=True)
        ) + math.fsum(
            (w - alpha_p * a) * (v + alpha_d * b)
            for w, a, v, b in zip(point.w, dx, point.v, dv, strict=True)
        )
        # Mehrotra's centring, (mu_aff / mu) cubed -- by multiplication: ``**`` is the
        # platform's pow, which is not correctly rounded everywhere, and one bit of
        # it moves every later iterate (CI's Linux cells found it, v4.3).
        ratio = affine / complementarity if complementarity > 0.0 else 0.0
        sigma = ratio * ratio * ratio
        target = sigma * mu
        # Corrector: centred, with the predictor's second-order terms.
        r_xz = [target - x * z - a * b for x, z, a, b in zip(point.x, point.z, dx, dz, strict=True)]
        r_wv = [target - w * v + a * b for w, v, a, b in zip(point.w, point.v, dx, dv, strict=True)]
        try:
            dx, dy, dz, dv = _direction(form, point, theta, factor, r_p, r_d, r_xz, r_wv)
        except _Overflow:
            return point, iteration, False, False
        alpha_p = _STEP * min(_boundary(point.x, dx), _boundary(point.w, [-d for d in dx]))
        alpha_d = _STEP * min(_boundary(point.z, dz), _boundary(point.v, dv))
        point.x = [x + alpha_p * d for x, d in zip(point.x, dx, strict=True)]
        point.w = [
            u - x if u - x > 0.0 else w * (1.0 - alpha_p)
            for u, x, w in zip(form.upper, point.x, point.w, strict=True)
        ]
        point.y = [y + alpha_d * d for y, d in zip(point.y, dy, strict=True)]
        point.z = [z + alpha_d * d for z, d in zip(point.z, dz, strict=True)]
        point.v = [v + alpha_d * d for v, d in zip(point.v, dv, strict=True)]
    return point, max_iterations, False, False


# --------------------------------------------------------------------------- #
# The exact finish and the certificate
# --------------------------------------------------------------------------- #


def _solve_dense(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; ``None`` if the matrix is singular."""

    size = len(rhs)
    work = [[*row, value] for row, value in zip(matrix, rhs, strict=True)]
    scale = max((abs(a) for row in matrix for a in row), default=0.0)
    floor = size * 2.220446049250313e-16 * scale
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(work[row][column]))
        if abs(work[pivot][column]) <= floor:
            return None
        work[column], work[pivot] = work[pivot], work[column]
        head = work[column]
        for row in range(column + 1, size):
            factor = work[row][column] / head[column]
            if factor != 0.0:
                work[row] = [a - factor * b for a, b in zip(work[row], head, strict=True)]
    solution = [0.0] * size
    for row in range(size - 1, -1, -1):
        total = work[row][size] - math.fsum(
            work[row][k] * solution[k] for k in range(row + 1, size)
        )
        solution[row] = total / work[row][row]
    return solution


def _independent(form: _Standard, order: Sequence[int], size: int) -> list[int] | None:
    """The first ``size`` columns in ``order`` that are linearly independent, or ``None``.

    Each column is reduced against those taken before it; one with nothing left
    is dependent on them and passed over.
    """

    pivots: list[tuple[int, list[float]]] = []
    chosen: list[int] = []
    for k in order:
        column = [0.0] * size
        for i, a in form.columns[k]:
            column[i] = a
        scale = _largest(column)
        if scale == 0.0:
            continue
        for row, vector in pivots:
            factor = column[row] / vector[row]
            if factor != 0.0:
                column = [c - factor * v for c, v in zip(column, vector, strict=True)]
        best = max(range(size), key=lambda i: abs(column[i]))
        if abs(column[best]) > 1e-9 * scale:
            pivots.append((best, column))
            chosen.append(k)
            if len(chosen) == size:
                return chosen
    return None


def _basis_solve(
    form: _Standard, chosen: Sequence[int], at_upper: Sequence[bool]
) -> tuple[list[float], list[float]] | None:
    """The vertex with ``chosen`` basic and every other variable at the bound read for it."""

    size = len(form.rows)
    index = {k: column for column, k in enumerate(chosen)}
    values = [
        0.0 if k in index or not at_upper[k] else form.upper[k] for k in range(len(form.costs))
    ]
    residual = [
        b - math.fsum(a * values[k] for k, a in row if k not in index)
        for b, row in zip(form.rhs, form.rows, strict=True)
    ]
    matrix = [[0.0] * size for _ in range(size)]
    for i, row in enumerate(form.rows):
        for k, a in row:
            if k in index:
                matrix[i][index[k]] = a
    solved = _solve_dense(matrix, residual)
    if solved is None:
        return None
    for column, k in enumerate(chosen):
        values[k] = solved[column]
    transposed = [[matrix[i][j] for i in range(size)] for j in range(size)]
    multipliers = _solve_dense(transposed, [form.costs[k] for k in chosen])
    if multipliers is None:
        return None
    return values, multipliers


def _vertex(form: _Standard, point: _Iterate) -> tuple[list[float], list[float]] | None:
    """A vertex on the basis the iterate shows, with its multipliers, or ``None``.

    Each variable is read as at its upper bound, at its lower, or basic. When
    the basic ones are as many as the rows and independent, their system is
    solved. Otherwise -- a degenerate vertex, with a basic variable at a bound,
    or an optimal face, with more inside than there are rows -- the basis is
    the first independent columns: the basic ones, then the others, each group
    by how far inside its range the iterate holds it. A basic variable left out
    sits at its nearer bound. A basic variable the solve puts outside its range
    is moved to the bound it crossed, and the basis chosen again.
    """

    size = len(form.rows)
    width = len(form.costs)
    at_upper = [w < v and w < x for x, w, v in zip(point.x, point.w, point.v, strict=True)]
    basic = [not upper and x > z for x, z, upper in zip(point.x, point.z, at_upper, strict=True)]
    inside = sorted(range(width), key=lambda k: (-min(point.x[k], point.w[k]) / form.upper[k], k))
    pinned: set[int] = set()
    for _ in range(_FINISH_ATTEMPTS):
        chosen = [k for k in range(width) if basic[k] and k not in pinned]
        solved = _basis_solve(form, chosen, at_upper) if len(chosen) == size else None
        if solved is None:
            order = [k for k in inside if basic[k] and k not in pinned]
            order += [k for k in inside if not basic[k] and k not in pinned]
            picked = _independent(form, order, size)
            if picked is None:
                return None
            chosen = picked
            taken = set(chosen)
            read = [
                at_upper[k]
                if not basic[k] or k in pinned or k in taken
                else point.w[k] < point.x[k]
                for k in range(width)
            ]
            solved = _basis_solve(form, chosen, read)
            if solved is None:
                return None
            at_upper = read
        values, multipliers = solved
        crossed = [k for k in chosen if values[k] < 0.0 or values[k] > form.upper[k]]
        if not crossed:
            return values, multipliers
        for k in crossed:
            pinned.add(k)
            basic[k] = False
            at_upper[k] = values[k] > form.upper[k]
    return None


@dataclass(frozen=True, slots=True)
class _Checked:
    feasible: bool
    violation: float
    objective: float
    bound: float
    gap: float


def _check(
    form: _Standard, x: Sequence[float], y: Sequence[float], tolerance: float, offset: float
) -> _Checked:
    """The certificate: ``x`` within the rows and box, and its objective near a proven bound.

    ``offset`` is the objective's constant from the shift to ``[0, u]`` and the fixed
    variables, so the objective, the bound and the gap are the program's own.
    """

    ax = _times(form, x)
    violation = max(
        _largest([b - value for b, value in zip(form.rhs, ax, strict=True)]),
        _largest(
            [max(-value, value - bound, 0.0) for value, bound in zip(x, form.upper, strict=True)]
        ),
    )
    d = [c - value for c, value in zip(form.costs, _transposed(form, y), strict=True)]
    bound = offset + math.fsum(
        [b * value for b, value in zip(form.rhs, y, strict=True)]
        + [min(0.0, reduced * upper) for reduced, upper in zip(d, form.upper, strict=True)]
    )
    objective = offset + math.fsum(c * value for c, value in zip(form.costs, x, strict=True))
    gap = max(objective - bound, 0.0) / (1.0 + abs(objective))
    return _Checked(violation <= tolerance, violation, objective, bound, gap)


def _farkas(form: _Standard, y: Sequence[float]) -> float:
    """By how much ``y'b`` exceeds the most ``y'Ax`` can be over the box ``[0, u]``."""

    g = _transposed(form, y)
    most = math.fsum(max(0.0, value) * upper for value, upper in zip(g, form.upper, strict=True))
    return math.fsum(b * value for b, value in zip(form.rhs, y, strict=True)) - most


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #


def _requires(
    feasibility_tolerance: float, convergence_tolerance: float, max_iterations: int
) -> None:
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
            f"max_iterations must be a positive integer, got {max_iterations!r}."
        )


def _per_constraint(
    program: LinearProgram, form: _Standard, y: Sequence[float], held: Mapping[int, float]
) -> tuple[float, ...]:
    """The rows' multipliers, and ``held``'s for constraints of fixed variables, by position."""

    values = [0.0] * len(program.constraints)
    for row, number in enumerate(form.source):
        values[number] = y[row]
    for number, value in held.items():
        values[number] = value
    return tuple(values)


def _named(program: LinearProgram, numbers: Sequence[int]) -> tuple[str, ...]:
    """The constraints' labels, each once, in the program's order."""

    return tuple(dict.fromkeys(program.constraints[n].label for n in sorted(set(numbers))))


def _candidates(
    form: _Standard, point: _Iterate
) -> list[tuple[LinearCertificate, list[float], list[float]]]:
    """What the certificate is checked on: the vertex, with its own multipliers and then
    with the iterate's -- the bound holds for any, and at a degenerate vertex the
    iterate's may prove what the basis's cannot -- and then the iterate itself."""

    candidates: list[tuple[LinearCertificate, list[float], list[float]]] = []
    vertex = _vertex(form, point)
    if vertex is not None:
        candidates.append((LinearCertificate.VERTEX, vertex[0], vertex[1]))
        candidates.append((LinearCertificate.VERTEX, vertex[0], point.y))
    candidates.append((LinearCertificate.INTERIOR, point.x, point.y))
    return candidates


@dataclass(frozen=True, slots=True)
class _Context:
    """What certifying a candidate reads besides the candidate."""

    program: LinearProgram
    form: _Standard
    fixed: Sequence[float]
    offset: float
    unmoved: float
    feasibility_tolerance: float
    convergence_tolerance: float


def _certified(
    context: _Context,
    candidates: Sequence[tuple[LinearCertificate, list[float], list[float]]],
    iterations: int,
) -> tuple[LinearSolution | None, float]:
    """The first candidate the certificate verifies, as a solution, and the least violation seen."""

    worst = math.inf
    for kind, x, y in candidates:
        checked = _check(context.form, x, y, context.feasibility_tolerance, context.offset)
        worst = min(worst, checked.violation)
        if checked.feasible and checked.gap <= context.convergence_tolerance:
            return (
                LinearSolution(
                    status=SolveStatus.OPTIMAL,
                    x=_unshift(context.program, context.form, context.fixed, x),
                    objective=checked.objective,
                    bound=checked.bound,
                    multipliers=_per_constraint(context.program, context.form, y, {}),
                    conflict=(),
                    certificate=kind,
                    iterations=iterations,
                    max_violation=max(checked.violation, context.unmoved),
                    gap=checked.gap,
                    detail=f"Certified at a {kind.value}: feasible within the tolerance, "
                    f"its objective within {checked.gap:.3g} (relative) of a proven bound.",
                ),
                worst,
            )
    return None, worst


def _unshift(
    program: LinearProgram, form: _Standard, fixed: Sequence[float], x: Sequence[float]
) -> tuple[float, ...]:
    values = list(fixed)
    for k, j in enumerate(form.origin):
        if j >= 0:
            values[j] = program.lower[j] + x[k]
    return tuple(values)


def solve_linear_program(
    program: LinearProgram,
    *,
    feasibility_tolerance: float,
    convergence_tolerance: float,
    max_iterations: int,
) -> LinearSolution:
    """Solve ``program`` and certify the answer. See the module docstring.

    Raises:
        OptimizationError: If a tolerance is not a positive finite number below
            one, or ``max_iterations`` is not a positive integer. An infeasible
            or unsolvable *program* is a status, not an exception.
    """

    _requires(feasibility_tolerance, convergence_tolerance, max_iterations)
    form, fixed = _standard(program)
    offset = math.fsum(
        cost * (bound if math.isnan(held) else held)
        for cost, bound, held in zip(program.costs, program.lower, fixed, strict=True)
    )
    limit = feasibility_tolerance
    missed = {number: sign for number, miss, sign in form.vacuous if miss > limit}
    if missed:
        # A constraint of fixed variables that misses its bound is its own Farkas
        # certificate: weighted by its sign, its bound exceeds what its (fixed)
        # variables reach by exactly the miss.
        margin = math.fsum(miss for number, miss, _ in form.vacuous if number in missed)
        return LinearSolution(
            status=SolveStatus.INFEASIBLE,
            x=None,
            objective=None,
            bound=None,
            multipliers=_per_constraint(program, form, [0.0] * len(form.rows), missed),
            conflict=_named(program, list(missed)),
            certificate=LinearCertificate.FARKAS,
            iterations=0,
            max_violation=max(miss for _, miss, _ in form.vacuous),
            gap=margin,
            detail="Infeasible: every variable of the constraints named is fixed, and they miss "
            f"their bounds by {margin:.6g} in all.",
        )
    context = _Context(
        program,
        form,
        fixed,
        offset,
        max((miss for _, miss, _ in form.vacuous), default=0.0),
        feasibility_tolerance,
        convergence_tolerance,
    )
    tolerance = min(feasibility_tolerance, convergence_tolerance)
    if not form.rows:
        # No row: each variable at the bound its cost prefers is optimal, exactly.
        preferred = [
            0.0 if cost >= 0.0 else bound
            for cost, bound in zip(form.costs, form.upper, strict=True)
        ]
        solution, _ = _certified(context, [(LinearCertificate.VERTEX, preferred, [])], 0)
        assert solution is not None  # the bound equals the objective at this point
        return solution
    point, iterations, converged, stalled = _interior_point(
        form, tolerance, max_iterations, watch=True
    )
    solution, worst = _certified(context, _candidates(form, point), iterations)
    if solution is not None:
        return solution
    # No certified optimum: is the program infeasible?
    phase, _ = _standard(program, phase_one=True)
    trial, more, _, _ = _interior_point(phase, tolerance, max_iterations)
    iterations += more
    margin = _farkas(form, trial.y)
    # Any point within the tolerance of every row would leave a margin of at most
    # the tolerance times the multipliers' total weight; more than that proves none.
    if margin > feasibility_tolerance * math.fsum(abs(value) for value in trial.y):
        # The rows the certificate weights, relative to its largest weight: a
        # certificate is a direction, and its scale proves nothing.
        weight = _largest(trial.y)
        weighted = [
            number
            for number, value in zip(form.source, trial.y, strict=True)
            if abs(value) > 1e-6 * weight
        ]
        return LinearSolution(
            status=SolveStatus.INFEASIBLE,
            x=None,
            objective=None,
            bound=None,
            multipliers=_per_constraint(program, form, trial.y, {}),
            conflict=_named(program, weighted),
            certificate=LinearCertificate.FARKAS,
            iterations=iterations,
            max_violation=worst,
            gap=margin,
            detail=f"Infeasible: the rows weighted by the certificate cannot hold together "
            f"within the bounds, by {margin:.6g}.",
        )
    if stalled and iterations < max_iterations:
        # It stalled, and Phase I proves no infeasibility: it resumes where it stopped,
        # within what the budget has left, and takes the steps it would have taken.
        point, more, converged, _ = _interior_point(
            form, tolerance, max_iterations - iterations, start=point
        )
        iterations += more
        solution, later = _certified(context, _candidates(form, point), iterations)
        if solution is not None:
            return solution
        worst = min(worst, later)
    status = (
        SolveStatus.ITERATION_LIMIT
        if not converged and iterations >= max_iterations
        else SolveStatus.NUMERICAL_FAILURE
    )
    return LinearSolution(
        status=status,
        x=None,
        objective=None,
        bound=None,
        multipliers=(),
        conflict=(),
        certificate=None,
        iterations=iterations,
        max_violation=worst,
        gap=math.inf,
        detail="No point the certificate verifies was reached, and no proof of infeasibility.",
    )
