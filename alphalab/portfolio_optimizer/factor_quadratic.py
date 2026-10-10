"""A factor-structured interior-point solver for large construction problems (ledger PRF-005).

:mod:`~alphalab.portfolio_optimizer.quadratic` solves every construction
program by Goldfarb and Idnani's dual active-set method: exact, deterministic,
and ``O(n^2)`` per add-or-drop step over a number of steps that grows with the
universe -- ``O(n^3)`` in all. Measured in pure Python on a long-only
mean-variance program with five factors: 0.13 s at 100 assets, 1.25 s at 200,
11.8 s at 400 and 134.6 s at 800, the time growing about elevenfold per
doubling.

When the covariance is a factor model's, ``C = B F B' + D`` with ``k`` factors
(:class:`~alphalab.analytics.risk_model.FactorStructure`), every linear system
such a program needs can be solved in ``O(n k^2)`` instead. This module solves

.. code-block:: text

    minimize    1/2 x' G x + a' x,      G = scale * (B F B' + D)
    subject to  the linear constraints and absolute-sum limits of
                quadratic.QuadraticProgram, unchanged

in two stages, and certifies the answer by the same criteria as the dense
method. Measured on the same programs: 0.01 s at 100 assets, 0.03 s at 400,
0.14 s at 800, 0.60 s at 3,200 and 1.61 s at 10,000 -- between 17 and 33
steps throughout. Construction (:func:`~alphalab.portfolio_optimizer.construction.construct`)
uses it for a covariance that carries its factor structure over
``FACTOR_STRUCTURED_MINIMUM_ASSETS`` or more assets; what then dominates a
construction is the dense covariance matrix itself -- building it, its
identity, its Euler decomposition -- which every consumer of a covariance
reads.

Stage one: a primal-dual interior point
---------------------------------------

Mehrotra's predictor-corrector method (Mehrotra, *SIAM Journal on
Optimization* 2, 1992; Wright, *Primal-Dual Interior-Point Methods*, 1997,
chapter 10), whose iteration count does not grow with ``n``. An absolute-sum
limit ``sum_k |x_k - c_k| <= L`` takes one auxiliary variable per index,
``t_k >= |x_k - c_k|``, and ``sum_k t_k <= L``. Each Newton system is then

* **block diagonal per asset** -- the curvature's diagonal, every one-variable
  inequality, and the two rows binding ``t_k`` to ``x_k``, all eliminated
  asset by asset. The eliminated diagonal is computed as
  ``scale * D_k + sum w q^2 + sum 4 w_a w_b / (w_a + w_b)``, never as a
  difference of large barrier weights;
* **plus the factors**, a rank-``k`` term inverted by the Woodbury identity in
  ``O(n k^2)``;
* **plus the coupling rows** -- equalities, inequalities over two or more
  variables, and each limit's sum row -- kept as unknowns with their own
  ``m x m`` Schur complement, which stays well conditioned as the iterates
  approach the boundary.

Stage two: an exact finish
--------------------------

An interior iterate is never exactly optimal: it approaches the optimum to a
tolerance. So once the iterates have converged far enough to say which
constraints bind, the method *finishes exactly*: every one-variable bound and
every absolute-sum kink that binds fixes its variable, every other binding
constraint is imposed as an equality, and that equality-constrained program --
still factor-structured -- is solved directly. Its multipliers are read off the
gradient; a fixed variable's is split between the absolute-sum kinks at it
(each within its limit's multiplier) and then the first declared bound that
binds it. The point is checked exactly as the dense method's is -- every
constraint residual within ``feasibility_tolerance``, the relative KKT
stationarity residual within ``convergence_tolerance`` -- and every inequality
multiplier must have the right sign. Where it does not, the binding set is
corrected (a violated constraint added, a wrongly signed one dropped) and the
finish repeated, a bounded number of times.

Status semantics
----------------

``OPTIMAL`` only when the finish certified the point. Otherwise
``ITERATION_LIMIT`` (the step budget ran out) or ``NUMERICAL_FAILURE`` (the
interior point did not converge, or no finish certified), with no ``x``. This
method never reports ``INFEASIBLE``: proving inconsistency, and naming the
constraints that conflict, is the dense method's. Construction hands any
program this method does not certify to the dense method
(:func:`~alphalab.portfolio_optimizer.construction.construct`).

Degeneracy
----------

Construction programs are often degenerate, and the finish is where that
shows: a gross-exposure limit equal to a long-only budget is the budget again;
a group bound can bind together with every bound of its members. Three rules
keep the finish exact there. Binding rows that are dependent over the free
variables leave the multipliers a family rather than a point; the finish takes
the member nearest the interior point's estimate, which lies inside the family
rather than at a corner where a multiplier changes sign. Such a row, not
imposed by the solve, is still checked -- met, and met exactly if it binds --
and when it is not, the variable on it held least firmly is released. And an
absolute-sum entry the solve carries across its center goes to the kink. In
the interior point itself the equality rows' share of the Schur complement is
regularized, by ``1e-8`` of its own diagonal, so a dependence that appears
only in the limit cannot make the complement singular.

Determinism
-----------

No random start, no clock, no hash order: IEEE-754 operations, square roots and
:func:`math.sumprod` in a fixed order, so the same program gives the same bits
anywhere. The dense and the structured method agree within the tolerances,
not to the bit -- which is why a construction problem's identity records which
of them it is solved by.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from alphalab.analytics.risk_model import FactorStructure, factor_cholesky_pivots
from alphalab.portfolio_optimizer.exceptions import OptimizationError
from alphalab.portfolio_optimizer.quadratic import (
    AbsoluteSumLimit,
    LinearConstraint,
    QuadraticSolution,
    SolveStatus,
    _finite,
    constraint_residuals,
)

__all__ = [
    "FactorCurvature",
    "FactorQuadraticProgram",
    "solve_factor_quadratic_program",
]

_EPSILON: Final = sys.float_info.epsilon
_sumprod: Final = math.sumprod

#: Fraction of the step to the boundary an iteration takes (Wright 1997, ch. 10).
_STEP_FRACTION: Final = 0.99
#: The interior point hands over to the exact finish once its scaled primal and
#: dual residuals and its average complementarity are all below this.
_FINISH_FROM: Final = 1e-6
#: Below this average complementarity the interior point stops: double
#: precision has nothing further to give.
_COMPLEMENTARITY_FLOOR: Final = 1e-14
#: Interior-point iterations allowed regardless of the step budget; a convex
#: program converges in far fewer, so reaching it means the data defeated it.
_INTERIOR_LIMIT: Final = 200
#: Active-set corrections one finish may make before it gives up.
_FINISH_ROUNDS: Final = 12
#: The finish's rank test: a binding row whose Cholesky pivot is at most this
#: fraction of its own diagonal -- within a millionth of a radian of the span of
#: the rows before it -- is dependent on them. Rounding leaves an exactly
#: dependent row a pivot of ``1e-16`` to ``1e-13`` of its diagonal, not zero.
_DEPENDENT: Final = 1e-12
#: Relative dual regularization of the equality rows in the coupling Schur
#: complement. A binding inequality can become dependent on an equality in the
#: limit -- a gross exposure limit equal to a long-only budget is the budget
#: again -- and the complement then turns singular exactly when the iterates
#: need it most. Adding this fraction of an equality's own diagonal keeps it
#: definite at a condition no worse than the reciprocal. Only equalities are
#: regularized: an inequality's row is tied to its complementarity, which a
#: perturbation the size of this would swamp near convergence, while an
#: equality's perturbed step merely leaves a residual the next step removes.
#: The exact finish settles the multipliers the dependent rows share.
_REGULARIZATION: Final = 1e-8
#: Iterations without halving ``max(primal, dual, complementarity)`` after which
#: the interior point is stalled -- what an inconsistent program looks like to it.
_STALL_ITERATIONS: Final = 25
#: A step shorter than this makes no progress in double precision.
_SHORTEST_STEP: Final = 1e-12


# --------------------------------------------------------------------------- #
# The program
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FactorCurvature:
    """``G = scale * (B F B' + diag(specific))``: a factor model's covariance, scaled.

    Attributes:
        loadings: ``B``, one row per variable and one column per factor.
        factor_covariance: ``F``, factors by factors, symmetric and positive
            semidefinite.
        specific: The diagonal ``D``, one entry per variable, each positive.
        scale: The positive multiple: a risk aversion, or ``1``.
        structure: The :class:`~alphalab.analytics.risk_model.FactorStructure`
            the three arrays above are, when they are -- the very same objects.
            A construction solves many programs over one covariance (every
            step of a volatility cap's search, every orthant of a costed one);
            what this type would check and derive again, the structure already
            has, so only the scale is checked. Arrays that are not the
            structure's own are checked in full.
        pivot_ratio: Smallest over largest natural-order Cholesky pivot of
            ``B F B' + D`` (:func:`~alphalab.analytics.risk_model.factor_cholesky_pivots`)
            -- the conditioning indicator the dense solver reports from its own
            factorization. Derived, not passed.

    Raises:
        OptimizationError: On a mis-shaped, non-finite or asymmetric input, a
            specific variance that is not positive, a factor covariance that is
            not positive semidefinite, or a scale that is not positive.
    """

    loadings: tuple[tuple[float, ...], ...]
    factor_covariance: tuple[tuple[float, ...], ...]
    specific: tuple[float, ...]
    scale: float
    structure: FactorStructure | None = field(default=None, repr=False, compare=False)
    pivot_ratio: float = field(init=False, compare=False)
    columns: tuple[tuple[float, ...], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not _finite(self.scale, "The curvature scale") > 0.0:
            raise OptimizationError(f"The curvature scale is {self.scale!r}; it must be positive.")
        structure = self.structure
        if (
            structure is not None
            and self.loadings is structure.loadings.values
            and self.factor_covariance is structure.factor_covariance.values
            and self.specific is structure.specific
        ):
            pivots = structure.pivots()
            if pivots is None:
                raise OptimizationError(
                    "This factor structure does not establish positive definiteness: a "
                    "specific variance is zero or the factor covariance is not positive "
                    "semidefinite."
                )
            object.__setattr__(self, "pivot_ratio", min(pivots) / max(pivots))
            object.__setattr__(self, "columns", structure.columns)
            return
        size = len(self.specific)
        width = len(self.factor_covariance)
        if size == 0 or width == 0:
            raise OptimizationError(
                "A factor curvature needs at least one variable and one factor."
            )
        if len(self.loadings) != size or any(len(row) != width for row in self.loadings):
            raise OptimizationError(
                f"The loadings must be {size} x {width}: one row per variable, one column per "
                "factor."
            )
        if any(len(row) != width for row in self.factor_covariance):
            raise OptimizationError("The factor covariance must be square.")
        for row in self.loadings:
            for value in row:
                _finite(value, "A loading")
        for first in range(width):
            for second in range(width):
                value = _finite(self.factor_covariance[first][second], "A factor covariance")
                if value != self.factor_covariance[second][first]:
                    raise OptimizationError(
                        f"The factor covariance is not symmetric at ({first}, {second})."
                    )
        for index, value in enumerate(self.specific):
            if not _finite(value, f"specific variance {index}") > 0.0:
                raise OptimizationError(
                    f"Specific variance {index} is {value!r}; the structured method needs every "
                    "one positive, which is what makes B F B' + D positive definite."
                )
        pivots = factor_cholesky_pivots(self.loadings, self.factor_covariance, self.specific)
        if pivots is None:
            raise OptimizationError("The factor covariance is not positive semidefinite.")
        object.__setattr__(self, "pivot_ratio", min(pivots) / max(pivots))
        object.__setattr__(
            self,
            "columns",
            tuple(tuple(row[factor] for row in self.loadings) for factor in range(width)),
        )

    @classmethod
    def of_structure(cls, structure: FactorStructure, scale: float) -> FactorCurvature:
        """``scale`` times the covariance ``structure`` implies.

        Raises:
            OptimizationError: If the structure does not establish positive
                definiteness, or the scale is not positive.
        """

        return cls(
            structure.loadings.values,
            structure.factor_covariance.values,
            structure.specific,
            scale,
            structure,
        )

    def times(self, x: Sequence[float]) -> list[float]:
        """``G x`` in ``O(n k)``."""

        exposures = [_sumprod(column, x) for column in self.columns]
        weighted = [_sumprod(row, exposures) for row in self.factor_covariance]
        return [
            self.scale * (variance * value + _sumprod(row, weighted))
            for variance, value, row in zip(self.specific, x, self.loadings, strict=True)
        ]


@dataclass(frozen=True, slots=True)
class FactorQuadraticProgram:
    """``minimize 1/2 x' G x + a' x`` with a factor-structured ``G``.

    The constraints are :class:`~alphalab.portfolio_optimizer.quadratic.QuadraticProgram`'s,
    unchanged, and :func:`~alphalab.portfolio_optimizer.quadratic.constraint_residuals`
    is the definition of satisfying them.

    Attributes:
        curvature: ``G``.
        linear: ``a``.
        constraints: Linear equalities and inequalities.
        absolute_sums: Absolute-sum limits.
    """

    curvature: FactorCurvature
    linear: tuple[float, ...]
    constraints: tuple[LinearConstraint, ...] = ()
    absolute_sums: tuple[AbsoluteSumLimit, ...] = ()

    def __post_init__(self) -> None:
        size = len(self.curvature.specific)
        if len(self.linear) != size:
            raise OptimizationError(
                f"{len(self.linear)} linear coefficients for a {size}-variable curvature."
            )
        for index, value in enumerate(self.linear):
            _finite(value, f"linear coefficient {index}")
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


# --------------------------------------------------------------------------- #
# Small dense linear algebra: k x k and m x m, never n x n
# --------------------------------------------------------------------------- #


def _lu(matrix: Sequence[Sequence[float]]) -> tuple[list[list[float]], list[int]] | None:
    """LU with partial pivoting (first largest on a tie); ``None`` if singular."""

    size = len(matrix)
    work = [list(row) for row in matrix]
    order = list(range(size))
    for column in range(size):
        pivot_row = max(range(column, size), key=lambda row: (abs(work[row][column]), -row))
        if work[pivot_row][column] == 0.0:
            return None
        if pivot_row != column:
            work[column], work[pivot_row] = work[pivot_row], work[column]
            order[column], order[pivot_row] = order[pivot_row], order[column]
        pivot = work[column][column]
        top = work[column]
        for row in range(column + 1, size):
            below = work[row]
            factor = below[column] / pivot
            below[column] = factor
            if factor != 0.0:
                for index in range(column + 1, size):
                    below[index] -= factor * top[index]
    return work, order


def _lu_solve(factors: tuple[list[list[float]], list[int]], rhs: Sequence[float]) -> list[float]:
    work, order = factors
    size = len(work)
    solution = [rhs[order[index]] for index in range(size)]
    for row in range(size):
        solution[row] -= _sumprod(work[row][:row], solution[:row])
    for row in range(size - 1, -1, -1):
        solution[row] = (
            solution[row] - _sumprod(work[row][row + 1 :], solution[row + 1 :])
        ) / work[row][row]
    return solution


@dataclass(slots=True)
class _Cholesky:
    """``L L'`` of a symmetric positive semidefinite ``m x m`` matrix, skipping dependence.

    A pivot that falls to ``dependent`` times its own diagonal (by default
    ``m * eps``) marks its row as linearly dependent on the rows before it: the
    row is skipped and its unknown is zero -- the standard treatment of a
    redundant constraint row.
    """

    lower: list[list[float]]
    skipped: list[bool]

    @classmethod
    def of(cls, matrix: Sequence[Sequence[float]], dependent: float | None = None) -> _Cholesky:
        size = len(matrix)
        threshold = size * _EPSILON if dependent is None else dependent
        lower = [[0.0] * size for _ in range(size)]
        skipped = [False] * size
        for column in range(size):
            row_entries = lower[column]
            total = matrix[column][column] - _sumprod(row_entries[:column], row_entries[:column])
            if total <= threshold * abs(matrix[column][column]) or total <= 0.0:
                skipped[column] = True
                continue
            root = math.sqrt(total)
            row_entries[column] = root
            for row in range(column + 1, size):
                below = lower[row]
                below[column] = (
                    matrix[row][column] - _sumprod(below[:column], row_entries[:column])
                ) / root
        return cls(lower, skipped)

    def solve(self, rhs: Sequence[float]) -> list[float]:
        size = len(rhs)
        lower = self.lower
        forward = [0.0] * size
        for row in range(size):
            if self.skipped[row]:
                continue
            forward[row] = (rhs[row] - _sumprod(lower[row][:row], forward[:row])) / lower[row][row]
        solution = [0.0] * size
        for row in range(size - 1, -1, -1):
            if self.skipped[row]:
                continue
            total = forward[row] - math.fsum(
                lower[later][row] * solution[later] for later in range(row + 1, size)
            )
            solution[row] = total / lower[row][row]
        return solution


class _Woodbury:
    """``(diag(delta) + scale * B F B')^-1`` over the coordinates with ``delta`` given.

    A coordinate whose inverse diagonal is zero is excluded: the operator maps it
    to zero and nothing else reads it -- how the finish holds a variable fixed.
    """

    __slots__ = ("_curvature", "_factors", "_inverse", "_scale")

    def __init__(self, curvature: FactorCurvature, inverse: list[float], scale: float) -> None:
        self._curvature = curvature
        self._inverse = inverse
        self._scale = scale
        columns = curvature.columns
        weighted = [[p * b for p, b in zip(inverse, column, strict=True)] for column in columns]
        width = len(columns)
        product = [[_sumprod(weighted[g], columns[h]) for h in range(width)] for g in range(width)]
        factor_rows = curvature.factor_covariance
        capacitance = [
            [
                (1.0 if g == h else 0.0)
                + scale * _sumprod(factor_rows[g], [product[a][h] for a in range(width)])
                for h in range(width)
            ]
            for g in range(width)
        ]
        factors = _lu(capacitance)
        if factors is None:  # I + C S has every eigenvalue at least one; only overflow gets here
            raise OptimizationError("The factor capacitance matrix is singular.")
        self._factors = factors

    def solve(self, rhs: Sequence[float]) -> list[float]:
        inverse = self._inverse
        first = [p * value for p, value in zip(inverse, rhs, strict=True)]
        exposures = [_sumprod(column, first) for column in self._curvature.columns]
        pushed = [
            self._scale * _sumprod(row, exposures) for row in self._curvature.factor_covariance
        ]
        correction = _lu_solve(self._factors, pushed)
        return [
            value - p * _sumprod(row, correction)
            for value, p, row in zip(first, inverse, self._curvature.loadings, strict=True)
        ]


# --------------------------------------------------------------------------- #
# The program's rows, sorted by how the Newton system treats them
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Layout:
    """Rows by kind, and the flat order of every inequality's slack and multiplier.

    Inequalities, flat: one-variable rows, rows of two or more variables, then
    per absolute-sum entry ``t - (x - c) >= 0`` and ``t + (x - c) >= 0``, then
    each limit's ``-sum t >= -L``. Coupling rows -- the ones the Newton system
    keeps as unknowns -- are the equalities, the rows of two or more variables
    and the sum rows, in that order.
    """

    size: int
    simple_origin: list[int] = field(default_factory=list)
    simple_index: list[int] = field(default_factory=list)
    simple_q: list[float] = field(default_factory=list)
    simple_b: list[float] = field(default_factory=list)
    general_origin: list[int] = field(default_factory=list)
    general_index: list[list[int]] = field(default_factory=list)
    general_coef: list[list[float]] = field(default_factory=list)
    general_b: list[float] = field(default_factory=list)
    equality_origin: list[int] = field(default_factory=list)
    equality_index: list[list[int]] = field(default_factory=list)
    equality_coef: list[list[float]] = field(default_factory=list)
    equality_b: list[float] = field(default_factory=list)
    family_start: list[int] = field(default_factory=list)
    family_stop: list[int] = field(default_factory=list)
    family_limit: list[float] = field(default_factory=list)
    entry_coordinate: list[int] = field(default_factory=list)
    entry_center: list[float] = field(default_factory=list)
    entry_family: list[int] = field(default_factory=list)
    simple_on: list[list[int]] = field(default_factory=list)
    entries_on: list[list[int]] = field(default_factory=list)

    @property
    def simple_count(self) -> int:
        return len(self.simple_q)

    @property
    def general_count(self) -> int:
        return len(self.general_b)

    @property
    def entry_count(self) -> int:
        return len(self.entry_center)

    @property
    def family_count(self) -> int:
        return len(self.family_limit)

    @property
    def base_above(self) -> int:
        return self.simple_count + self.general_count

    @property
    def base_below(self) -> int:
        return self.base_above + self.entry_count

    @property
    def base_sum(self) -> int:
        return self.base_below + self.entry_count

    @property
    def inequality_count(self) -> int:
        return self.base_sum + self.family_count

    @property
    def coupling_count(self) -> int:
        return len(self.equality_b) + self.general_count + self.family_count


def _layout(program: FactorQuadraticProgram) -> _Layout:
    size = len(program.linear)
    layout = _Layout(size)
    layout.simple_on = [[] for _ in range(size)]
    layout.entries_on = [[] for _ in range(size)]
    for origin, constraint in enumerate(program.constraints):
        indices = [index for index, _ in constraint.terms]
        coefficients = [coefficient for _, coefficient in constraint.terms]
        if constraint.equality:
            layout.equality_origin.append(origin)
            layout.equality_index.append(indices)
            layout.equality_coef.append(coefficients)
            layout.equality_b.append(constraint.bound)
        elif len(indices) == 1:
            layout.simple_on[indices[0]].append(layout.simple_count)
            layout.simple_origin.append(origin)
            layout.simple_index.append(indices[0])
            layout.simple_q.append(coefficients[0])
            layout.simple_b.append(constraint.bound)
        else:
            layout.general_origin.append(origin)
            layout.general_index.append(indices)
            layout.general_coef.append(coefficients)
            layout.general_b.append(constraint.bound)
    for number, family in enumerate(program.absolute_sums):
        layout.family_start.append(layout.entry_count)
        for index, center in zip(family.indices, family.centers, strict=True):
            layout.entries_on[index].append(layout.entry_count)
            layout.entry_coordinate.append(index)
            layout.entry_center.append(center)
            layout.entry_family.append(number)
        layout.family_stop.append(layout.entry_count)
        layout.family_limit.append(family.limit)
    return layout


def _row_values(layout: _Layout, x: Sequence[float], t: Sequence[float]) -> list[float]:
    """``n_j' v`` for every inequality, in the flat order (no bounds subtracted)."""

    values = [q * x[index] for q, index in zip(layout.simple_q, layout.simple_index, strict=True)]
    values.extend(
        _sumprod(coefficients, [x[index] for index in indices])
        for indices, coefficients in zip(layout.general_index, layout.general_coef, strict=True)
    )
    coordinates = layout.entry_coordinate
    values.extend(t[entry] - x[coordinates[entry]] for entry in range(layout.entry_count))
    values.extend(t[entry] + x[coordinates[entry]] for entry in range(layout.entry_count))
    values.extend(
        -math.fsum(t[start:stop])
        for start, stop in zip(layout.family_start, layout.family_stop, strict=True)
    )
    return values


def _row_bounds(layout: _Layout) -> list[float]:
    return [
        *layout.simple_b,
        *layout.general_b,
        *(-center for center in layout.entry_center),
        *layout.entry_center,
        *(-limit for limit in layout.family_limit),
    ]


def _equality_values(layout: _Layout, x: Sequence[float]) -> list[float]:
    return [
        _sumprod(coefficients, [x[index] for index in indices])
        for indices, coefficients in zip(layout.equality_index, layout.equality_coef, strict=True)
    ]


# --------------------------------------------------------------------------- #
# Stage one: the interior point
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Iterate:
    x: list[float]
    t: list[float]
    y: list[float]  # equality multipliers
    z: list[float]  # inequality multipliers, flat order
    s: list[float]  # inequality slacks, flat order


class _Scaled:
    """The program with its objective divided by ``omega``: same minimizer, unit-sized data."""

    __slots__ = ("curvature", "layout", "linear", "omega", "program", "sigma")

    def __init__(self, program: FactorQuadraticProgram) -> None:
        curvature = program.curvature
        largest_variance = max(
            curvature.scale
            * (
                variance
                + _sumprod(row, [_sumprod(f_row, row) for f_row in curvature.factor_covariance])
            )
            for variance, row in zip(curvature.specific, curvature.loadings, strict=True)
        )
        largest_linear = max((abs(value) for value in program.linear), default=0.0)
        self.omega = max(largest_variance, largest_linear)
        self.program = program
        self.curvature = curvature
        self.sigma = curvature.scale / self.omega
        self.linear = [value / self.omega for value in program.linear]
        self.layout = _layout(program)

    def gradient(self, x: Sequence[float]) -> list[float]:
        """``G x / omega + a / omega``."""

        exposures = [_sumprod(column, x) for column in self.curvature.columns]
        weighted = [_sumprod(row, exposures) for row in self.curvature.factor_covariance]
        sigma = self.sigma
        return [
            sigma * (variance * value + _sumprod(row, weighted)) + linear
            for variance, value, row, linear in zip(
                self.curvature.specific, x, self.curvature.loadings, self.linear, strict=True
            )
        ]


class _Newton:
    """One factorization of the interior point's Newton system, solved twice per step."""

    __slots__ = ("_couple", "_coupling_t", "_coupling_x", "_layout", "_ptt", "_pxt", "_woodbury")

    def __init__(self, scaled: _Scaled, iterate: _Iterate) -> None:
        layout = scaled.layout
        self._layout = layout
        z, s = iterate.z, iterate.s
        w = [zj / sj for zj, sj in zip(z, s, strict=True)]
        above, below = layout.base_above, layout.base_below
        sigma = scaled.sigma
        delta = [sigma * variance for variance in scaled.curvature.specific]
        for row, (index, q) in enumerate(zip(layout.simple_index, layout.simple_q, strict=True)):
            delta[index] += w[row] * q * q
        pxt: list[float] = []
        ptt: list[float] = []
        for entry, index in enumerate(layout.entry_coordinate):
            wa, wb = w[above + entry], w[below + entry]
            pxt.append(wb - wa)
            ptt.append(wa + wb)
            delta[index] += 4.0 * wa * wb / (wa + wb)
        self._pxt = pxt
        self._ptt = ptt
        self._woodbury = _Woodbury(scaled.curvature, [1.0 / value for value in delta], sigma)

        # The coupling rows, and M0^-1 applied to each of them.
        rows_x: list[tuple[list[int], list[float]]] = []
        rows_t: list[tuple[int, int]] = []  # sum rows: (start, stop) of their t entries
        rows_x.extend(zip(layout.equality_index, layout.equality_coef, strict=True))
        rows_x.extend(zip(layout.general_index, layout.general_coef, strict=True))
        rows_t.extend(zip(layout.family_start, layout.family_stop, strict=True))
        self._coupling_x = rows_x
        self._coupling_t = rows_t
        count = layout.coupling_count
        base = layout.simple_count
        omega = [0.0] * len(layout.equality_b)
        omega.extend(s[base + g] / z[base + g] for g in range(layout.general_count))
        omega.extend(
            s[layout.base_sum + f] / z[layout.base_sum + f] for f in range(layout.family_count)
        )
        columns: list[tuple[list[float], list[float]]] = []
        for indices, coefficients in rows_x:
            rx = [0.0] * layout.size
            for index, coefficient in zip(indices, coefficients, strict=True):
                rx[index] = coefficient
            columns.append(self.apply(rx, [0.0] * layout.entry_count))
        for start, stop in rows_t:
            rt = [0.0] * layout.entry_count
            for entry in range(start, stop):
                rt[entry] = -1.0
            columns.append(self.apply([0.0] * layout.size, rt))
        schur = [[0.0] * count for _ in range(count)]
        for i in range(count):
            for j in range(i, count):
                value = self._row_dot(i, columns[j][0], columns[j][1])
                schur[i][j] = value
                schur[j][i] = value
            schur[i][i] += omega[i] if omega[i] > 0.0 else _REGULARIZATION * schur[i][i]
        self._couple = (columns, _Cholesky.of(schur))

    def apply(self, rx: Sequence[float], rt: Sequence[float]) -> tuple[list[float], list[float]]:
        """``M0^-1 (rx, rt)``: eliminate each ``t`` into its asset, Woodbury, recover ``t``."""

        layout = self._layout
        reduced = list(rx)
        pxt, ptt = self._pxt, self._ptt
        coordinates = layout.entry_coordinate
        for entry in range(layout.entry_count):
            reduced[coordinates[entry]] -= pxt[entry] / ptt[entry] * rt[entry]
        dx = self._woodbury.solve(reduced)
        dt = [
            (rt[entry] - pxt[entry] * dx[coordinates[entry]]) / ptt[entry]
            for entry in range(layout.entry_count)
        ]
        return dx, dt

    def _row_dot(self, row: int, vx: Sequence[float], vt: Sequence[float]) -> float:
        if row < len(self._coupling_x):
            indices, coefficients = self._coupling_x[row]
            return _sumprod(coefficients, [vx[index] for index in indices])
        start, stop = self._coupling_t[row - len(self._coupling_x)]
        return -math.fsum(vt[start:stop])

    def solve(
        self, rx: Sequence[float], rt: Sequence[float], rl: Sequence[float]
    ) -> tuple[list[float], list[float], list[float]]:
        """``[M0 -R'; R Omega] [dv; dl] = [r; rl]`` by the Schur complement on the coupling rows."""

        yx, yt = self.apply(rx, rt)
        columns, factor = self._couple
        rhs = [rl[row] - self._row_dot(row, yx, yt) for row in range(len(rl))]
        dl = factor.solve(rhs)
        for row, weight in enumerate(dl):
            if weight == 0.0:
                continue
            cx, ct = columns[row]
            yx = [a + weight * b for a, b in zip(yx, cx, strict=True)]
            if ct:
                yt = [a + weight * b for a, b in zip(yt, ct, strict=True)]
        return yx, yt, dl


def _dual_residual(scaled: _Scaled, it: _Iterate) -> tuple[list[float], list[float]]:
    """``G x + a - E'y - N'z`` over ``x``, and its ``t`` part, both scaled."""

    layout = scaled.layout
    rx = scaled.gradient(it.x)
    z = it.z
    for row, (index, q) in enumerate(zip(layout.simple_index, layout.simple_q, strict=True)):
        rx[index] -= z[row] * q
    base = layout.simple_count
    for g, (indices, coefficients) in enumerate(
        zip(layout.general_index, layout.general_coef, strict=True)
    ):
        multiplier = z[base + g]
        for index, coefficient in zip(indices, coefficients, strict=True):
            rx[index] -= multiplier * coefficient
    for e, (indices, coefficients) in enumerate(
        zip(layout.equality_index, layout.equality_coef, strict=True)
    ):
        multiplier = it.y[e]
        for index, coefficient in zip(indices, coefficients, strict=True):
            rx[index] -= multiplier * coefficient
    above, below, base_sum = layout.base_above, layout.base_below, layout.base_sum
    rt = [0.0] * layout.entry_count
    for entry, index in enumerate(layout.entry_coordinate):
        za, zb = z[above + entry], z[below + entry]
        rx[index] += za - zb
        rt[entry] = -za - zb + z[base_sum + layout.entry_family[entry]]
    return rx, rt


def _newton_rhs(
    scaled: _Scaled,
    it: _Iterate,
    residuals: tuple[list[float], list[float], list[float], list[float]],
    target: Sequence[float],
) -> tuple[list[float], list[float], list[float], list[float]]:
    """The reduced right-hand sides for complementarity target ``s z -> -target``.

    Returns ``(rho_x, rho_t, rho_lambda, g)`` with ``g`` the eliminated rows'
    constant term (``dz = g - w n'dv``), zero for the coupling rows.
    """

    layout = scaled.layout
    rx, rt, rp, re = residuals
    z, s = it.z, it.s
    g = [(-target[j] - z[j] * rp[j]) / s[j] for j in range(layout.inequality_count)]
    rho_x = [-value for value in rx]
    rho_t = [-value for value in rt]
    for row, (index, q) in enumerate(zip(layout.simple_index, layout.simple_q, strict=True)):
        rho_x[index] += g[row] * q
    above, below = layout.base_above, layout.base_below
    for entry, index in enumerate(layout.entry_coordinate):
        ga, gb = g[above + entry], g[below + entry]
        rho_x[index] += gb - ga
        rho_t[entry] += ga + gb
    rho_l = [-value for value in re]
    base = layout.simple_count
    rho_l.extend(
        -target[base + j] / z[base + j] - rp[base + j] for j in range(layout.general_count)
    )
    base_sum = layout.base_sum
    rho_l.extend(
        -target[base_sum + f] / z[base_sum + f] - rp[base_sum + f]
        for f in range(layout.family_count)
    )
    for j in range(layout.simple_count, layout.base_above):
        g[j] = 0.0
    for j in range(base_sum, layout.inequality_count):
        g[j] = 0.0
    return rho_x, rho_t, rho_l, g


def _direction(
    scaled: _Scaled,
    it: _Iterate,
    newton: _Newton,
    residuals: tuple[list[float], list[float], list[float], list[float]],
    target: Sequence[float],
) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    """``(dx, dt, dy, dz, ds)`` for one complementarity target."""

    layout = scaled.layout
    rho_x, rho_t, rho_l, g = _newton_rhs(scaled, it, residuals, target)
    dx, dt, dl = newton.solve(rho_x, rho_t, rho_l)
    rp = residuals[2]
    moved = _row_values(layout, dx, dt)
    ds = [value + residual for value, residual in zip(moved, rp, strict=True)]
    z, s = it.z, it.s
    dz = [g[j] - z[j] / s[j] * moved[j] for j in range(layout.inequality_count)]
    equalities = len(layout.equality_b)
    base = layout.simple_count
    for j in range(layout.general_count):
        dz[base + j] = dl[equalities + j]
    base_sum = layout.base_sum
    for f in range(layout.family_count):
        dz[base_sum + f] = dl[equalities + layout.general_count + f]
    return dx, dt, dl[:equalities], dz, ds


def _longest(values: Sequence[float], steps: Sequence[float]) -> float:
    """The largest ``alpha`` in ``(0, 1]`` keeping ``values + alpha * steps`` non-negative."""

    alpha = 1.0
    for value, step in zip(values, steps, strict=True):
        if step < 0.0:
            alpha = min(alpha, -value / step)
    return alpha


def _start(scaled: _Scaled) -> _Iterate:
    """Mehrotra's starting point: the least-squares point, shifted into the interior.

    Solves ``minimize 1/2 v'Hv + c'v + 1/2 ||N v - b||^2`` subject to the
    equalities -- the Newton system with every barrier weight one -- then moves
    the slacks and multipliers it implies to at least one (Vandenberghe,
    *The CVXOPT linear and quadratic cone program solvers*, 2010, section 6).
    """

    layout = scaled.layout
    count = layout.inequality_count
    unit = _Iterate(
        [0.0] * layout.size,
        [0.0] * layout.entry_count,
        [0.0] * len(layout.equality_b),
        [1.0] * count,
        [1.0] * count,
    )
    newton = _Newton(scaled, unit)
    bounds = _row_bounds(layout)
    rho_x = [-value for value in scaled.linear]
    rho_t = [0.0] * layout.entry_count
    for row, (index, q) in enumerate(zip(layout.simple_index, layout.simple_q, strict=True)):
        rho_x[index] += bounds[row] * q
    above, below = layout.base_above, layout.base_below
    for entry, index in enumerate(layout.entry_coordinate):
        ba, bb = bounds[above + entry], bounds[below + entry]
        rho_x[index] += bb - ba
        rho_t[entry] += ba + bb
    rho_l = list(layout.equality_b)
    rho_l.extend(bounds[layout.simple_count + g] for g in range(layout.general_count))
    rho_l.extend(bounds[layout.base_sum + f] for f in range(layout.family_count))
    x, t, dl = newton.solve(rho_x, rho_t, rho_l)
    values = _row_values(layout, x, t)
    s = [value - bound for value, bound in zip(values, bounds, strict=True)]
    z = [-value for value in s]
    if s:
        shift_s = max(-value for value in s)
        if shift_s >= 0.0:
            s = [value + 1.0 + shift_s for value in s]
        shift_z = max(-value for value in z)
        if shift_z >= 0.0:
            z = [value + 1.0 + shift_z for value in z]
    return _Iterate(x, t, dl[: len(layout.equality_b)], z, s)


def _residuals(
    scaled: _Scaled, it: _Iterate
) -> tuple[list[float], list[float], list[float], list[float]]:
    layout = scaled.layout
    rx, rt = _dual_residual(scaled, it)
    values = _row_values(layout, it.x, it.t)
    bounds = _row_bounds(layout)
    rp = [value - bound - slack for value, bound, slack in zip(values, bounds, it.s, strict=True)]
    re = [
        value - bound
        for value, bound in zip(_equality_values(layout, it.x), layout.equality_b, strict=True)
    ]
    return rx, rt, rp, re


# --------------------------------------------------------------------------- #
# Stage two: the exact finish
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Binding:
    """Which constraints bind: the finish's working hypothesis."""

    simple: set[int]  # one-variable rows
    general: set[int]  # rows of two or more variables
    families: dict[int, list[int]]  # limit -> sign per entry: +1, -1, or 0 at a kink

    def signature(self) -> tuple[object, ...]:
        return (
            tuple(sorted(self.simple)),
            tuple(sorted(self.general)),
            tuple(sorted((f, tuple(signs)) for f, signs in self.families.items())),
        )


def _identify(scaled: _Scaled, it: _Iterate) -> _Binding:
    """A constraint binds when its slack is below its multiplier (both scaled)."""

    layout = scaled.layout
    z, s = it.z, it.s
    simple = {row for row in range(layout.simple_count) if s[row] < z[row]}
    base = layout.simple_count
    general = {g for g in range(layout.general_count) if s[base + g] < z[base + g]}
    families: dict[int, list[int]] = {}
    above, below, base_sum = layout.base_above, layout.base_below, layout.base_sum
    for f in range(layout.family_count):
        if not s[base_sum + f] < z[base_sum + f]:
            continue
        signs: list[int] = []
        for entry in range(layout.family_start[f], layout.family_stop[f]):
            on_above = s[above + entry] < z[above + entry]
            on_below = s[below + entry] < z[below + entry]
            if on_above and on_below:
                signs.append(0)
            elif on_above or on_below:
                signs.append(1 if on_above else -1)
            else:
                offset = it.x[layout.entry_coordinate[entry]] - layout.entry_center[entry]
                signs.append(1 if offset >= 0.0 else -1)
        families[f] = signs
    return _Binding(simple, general, families)


@dataclass(slots=True)
class _Finished:
    x: list[float]
    active: tuple[str, ...]
    multipliers: dict[str, float]
    violation: float
    stationarity: float


@dataclass(slots=True)
class _Attempt:
    """One exact solve on a binding set, and what it says should change."""

    finished: _Finished | None
    add_simple: set[int] = field(default_factory=set)
    drop_simple: set[int] = field(default_factory=set)
    add_general: set[int] = field(default_factory=set)
    drop_general: set[int] = field(default_factory=set)
    add_families: dict[int, list[int]] = field(default_factory=dict)
    drop_families: set[int] = field(default_factory=set)
    #: (limit, position) -> the sign an entry moves to: a kink released to a
    #: side, or a side abandoned for the kink (``0``).
    resign: dict[tuple[int, int], int] = field(default_factory=dict)

    @property
    def changes(self) -> bool:
        return bool(
            self.add_simple
            or self.drop_simple
            or self.add_general
            or self.drop_general
            or self.add_families
            or self.drop_families
            or self.resign
        )


def _exact(
    scaled: _Scaled,
    binding: _Binding,
    it: _Iterate,
    feasibility_tolerance: float,
    convergence_tolerance: float,
) -> _Attempt | None:
    """Solve the equality-constrained program the binding set defines, and judge it.

    ``it`` supplies the interior point's multiplier estimates, which decide
    between multipliers the binding rows leave undetermined.

    ``None`` when it neither certifies nor finds anything to correct: the
    interior point should be taken further first.
    """

    layout = scaled.layout
    program = scaled.program
    size = layout.size
    simple_rows = sorted(binding.simple)

    # Fixed variables: one-variable rows, then kinks; the first value wins and a
    # row that would set another is dropped from the binding set.
    fixed: dict[int, float] = {}
    simple_kept: list[int] = []
    for row in simple_rows:
        index = layout.simple_index[row]
        value = layout.simple_b[row] / layout.simple_q[row]
        if index in fixed and abs(fixed[index] - value) > feasibility_tolerance:
            continue
        fixed.setdefault(index, value)
        simple_kept.append(row)
    kinks: dict[int, list[tuple[int, int]]] = {}  # coordinate -> (limit, position) kinked at it
    families: dict[int, list[int]] = {}
    for f, signs in sorted(binding.families.items()):
        kept = list(signs)
        for position, sign in enumerate(signs):
            if sign != 0:
                continue
            entry = layout.family_start[f] + position
            index = layout.entry_coordinate[entry]
            center = layout.entry_center[entry]
            if index in fixed and abs(fixed[index] - center) > feasibility_tolerance:
                kept[position] = 1 if fixed[index] > center else -1
                continue
            fixed.setdefault(index, center)
            kinks.setdefault(index, []).append((f, position))
        families[f] = kept
    free = [index for index in range(size) if index not in fixed]
    x_fixed = [fixed.get(index, 0.0) for index in range(size)]

    # The binding rows other than one-variable ones, as equalities n'x = b.
    rows: list[tuple[str, list[int], list[float], float, bool]] = []  # label, idx, coef, b, eq
    row_kind: list[tuple[str, int]] = []
    for e, origin in enumerate(layout.equality_origin):
        rows.append(
            (
                program.constraints[origin].label,
                layout.equality_index[e],
                layout.equality_coef[e],
                layout.equality_b[e],
                True,
            )
        )
        row_kind.append(("equality", e))
    for g in sorted(binding.general):
        rows.append(
            (
                program.constraints[layout.general_origin[g]].label,
                layout.general_index[g],
                layout.general_coef[g],
                layout.general_b[g],
                False,
            )
        )
        row_kind.append(("general", g))
    for f, signs in families.items():
        indices: list[int] = []
        coefficients: list[float] = []
        bound = -layout.family_limit[f]
        offsets: list[float] = []
        for position, sign in enumerate(signs):
            if sign == 0:
                continue
            entry = layout.family_start[f] + position
            indices.append(layout.entry_coordinate[entry])
            coefficients.append(-float(sign))
            offsets.append(sign * layout.entry_center[entry])
        bound -= math.fsum(offsets)
        order = sorted(range(len(indices)), key=indices.__getitem__)
        rows.append(
            (
                program.absolute_sums[f].label,
                [indices[i] for i in order],
                [coefficients[i] for i in order],
                bound,
                False,
            )
        )
        row_kind.append(("family", f))

    is_free = [index not in fixed for index in range(size)]
    reduced: list[tuple[list[int], list[float], float]] = []  # free part, rhs
    reduced_rows: list[int] = []
    for number, (_, indices, coefficients, bound, _) in enumerate(rows):
        free_indices: list[int] = []
        free_coefficients: list[float] = []
        moved: list[float] = []
        for index, coefficient in zip(indices, coefficients, strict=True):
            if is_free[index]:
                free_indices.append(index)
                free_coefficients.append(coefficient)
            else:
                moved.append(coefficient * x_fixed[index])
        rhs = bound - math.fsum(moved)
        reduced.append((free_indices, free_coefficients, rhs))
        reduced_rows.append(number)

    # minimize over the free variables: G_FF x_F + h_F - A_F' u = 0, A_F x_F = rhs.
    sigma = scaled.sigma
    inverse = [0.0 if index in fixed else 1.0 / (sigma * scaled.curvature.specific[index])
               for index in range(size)]  # fmt: skip
    woodbury = _Woodbury(scaled.curvature, inverse, sigma)
    h = scaled.gradient(x_fixed)
    for index in fixed:
        h[index] = 0.0
    q = woodbury.solve(h)
    columns: list[list[float]] = []
    for free_indices, free_coefficients, _ in reduced:
        dense = [0.0] * size
        for index, coefficient in zip(free_indices, free_coefficients, strict=True):
            dense[index] = coefficient
        columns.append(woodbury.solve(dense))
    count = len(reduced)
    schur = [[0.0] * count for _ in range(count)]
    for i, (free_indices, free_coefficients, _) in enumerate(reduced):
        for j in range(i, count):
            value = _sumprod(free_coefficients, [columns[j][index] for index in free_indices])
            schur[i][j] = value
            schur[j][i] = value
    rhs_reduced = [
        rhs + _sumprod(free_coefficients, [q[index] for index in free_indices])
        for free_indices, free_coefficients, rhs in reduced
    ]
    factor = _Cholesky.of(schur, _DEPENDENT)
    multipliers_scaled = factor.solve(rhs_reduced)
    x = list(x_fixed)
    for index in free:
        x[index] = -q[index]
    for weight, column in zip(multipliers_scaled, columns, strict=True):
        if weight != 0.0:
            for index in free:
                x[index] += weight * column[index]
    skipped = [row for row, flag in enumerate(factor.skipped) if flag]
    if skipped:
        multipliers_scaled = _nearest_multipliers(
            scaled,
            it,
            x,
            fixed,
            kinks,
            rows,
            row_kind,
            reduced_rows,
            schur,
            factor,
            multipliers_scaled,
        )

    # Multipliers in the program's own units. A binding inequality whose
    # multiplier came out negative is pulling the wrong way: it is counted at
    # zero, so the stationarity residual shows what releasing it would leave,
    # and it is named for release should the certificate fail.
    omega = scaled.omega
    attempt = _Attempt(None)
    row_multiplier = [0.0] * len(rows)
    for number, value in zip(reduced_rows, multipliers_scaled, strict=True):
        multiplier = omega * value
        if multiplier < 0.0 and not rows[number][4]:
            kind, which = row_kind[number]
            if kind == "general":
                attempt.drop_general.add(which)
            else:
                attempt.drop_families.add(which)
            multiplier = 0.0
        row_multiplier[number] = multiplier
    gradient = program.curvature.times(x)
    curvature_scale = max((abs(value) for value in gradient), default=0.0)
    residual = [value + linear for value, linear in zip(gradient, program.linear, strict=True)]
    constraint_scale = 0.0
    for number, (_, indices, coefficients, _, _) in enumerate(rows):
        multiplier = row_multiplier[number]
        if multiplier == 0.0:
            continue
        for index, coefficient in zip(indices, coefficients, strict=True):
            residual[index] -= multiplier * coefficient
            constraint_scale = max(constraint_scale, abs(multiplier * coefficient))

    labels: dict[str, float] = {}
    for number, (label, _, _, _, _) in enumerate(rows):
        labels[label] = labels.get(label, 0.0) + row_multiplier[number]
    family_multiplier = {
        which: row_multiplier[number]
        for number, (kind, which) in enumerate(row_kind)
        if kind == "family"
    }

    # A fixed variable's multiplier: kinks first, within their limit's multiplier,
    # then the first declared one-variable row on the side it pushes. ``hold``
    # is how firmly each fixed variable is held: the multiplier a bound carries,
    # or how far inside its limit's multiplier a kink's pressure is.
    simple_multiplier = dict.fromkeys(simple_kept, 0.0)
    rows_on: dict[int, list[int]] = {}
    for row in simple_kept:
        rows_on.setdefault(layout.simple_index[row], []).append(row)
    hold: dict[int, float] = {}
    pressures: dict[int, float] = {}
    for index in sorted(fixed):
        pressure = residual[index]
        pressures[index] = pressure
        capacity = math.fsum(
            max(0.0, family_multiplier.get(f, 0.0)) for f, _ in kinks.get(index, [])
        )
        absorbed = min(max(pressure, -capacity), capacity)
        remainder = pressure - absorbed
        residual[index] = remainder
        hold[index] = capacity - abs(absorbed)
        if capacity > 0.0:
            constraint_scale = max(constraint_scale, capacity)
        if remainder == 0.0:
            continue
        for row in rows_on.get(index, []):
            coefficient = layout.simple_q[row]
            if coefficient * remainder > 0.0:
                simple_multiplier[row] = remainder / coefficient
                residual[index] = 0.0
                hold[index] = abs(remainder)
                constraint_scale = max(constraint_scale, abs(remainder))
                break
        else:
            # Nothing binding at this variable can push the way the gradient
            # asks: it should move -- off its bound, or off its kink to the side
            # the gradient points away from.
            attempt.drop_simple.update(rows_on.get(index, []))
            for f, position in kinks.get(index, []):
                attempt.resign[(f, position)] = -1 if remainder > 0.0 else 1
    for row, multiplier in simple_multiplier.items():
        label = program.constraints[layout.simple_origin[row]].label
        labels[label] = labels.get(label, 0.0) + multiplier

    scale = max(
        1.0,
        curvature_scale,
        max((abs(value) for value in program.linear), default=0.0),
        constraint_scale,
    )
    stationarity = max((abs(value) for value in residual), default=0.0) / scale
    residuals = constraint_residuals(program, x)
    violation = max(residuals.values(), default=0.0)

    # A binding row the solve did not impose -- dependent on the others over the
    # free variables -- must still be met, and met exactly if it binds (an
    # equality, or a positive multiplier). One that is not means too many
    # variables were fixed for the rows to be consistent.
    over_fixed = []
    for row in skipped:
        number = reduced_rows[row]
        _, indices, coefficients, bound, equality = rows[number]
        value = math.fsum(
            coefficient * x[index] for index, coefficient in zip(indices, coefficients, strict=True)
        )
        tight = equality or row_multiplier[number] > 0.0
        if value - bound < -feasibility_tolerance or (
            tight and abs(value - bound) > feasibility_tolerance
        ):
            over_fixed.append(number)

    if (
        violation <= feasibility_tolerance
        and stationarity <= convergence_tolerance
        and not over_fixed
    ):
        active: list[str] = []
        binding_origins = {layout.simple_origin[row] for row in simple_kept}
        binding_origins.update(layout.general_origin[g] for g in binding.general)
        binding_origins.update(layout.equality_origin)
        for origin, constraint in enumerate(program.constraints):
            if origin in binding_origins:
                active.append(constraint.label)
        active.extend(program.absolute_sums[f].label for f in sorted(families))
        clean = {label: (0.0 if value == 0.0 else value) for label, value in sorted(labels.items())}
        attempt.finished = _Finished(x, tuple(active), clean, violation, stationarity)
        return attempt

    # Not certified: say what should change. An over-fixed row releases the
    # variable on it held least firmly.
    for number in over_fixed:
        candidates = [index for index in rows[number][1] if index in fixed]
        if not candidates:
            if row_kind[number][0] == "general":
                attempt.drop_general.add(row_kind[number][1])
            elif row_kind[number][0] == "family":
                attempt.drop_families.add(row_kind[number][1])
            continue
        weakest = min(candidates, key=lambda index: (hold.get(index, 0.0), index))
        attempt.drop_simple.update(rows_on.get(weakest, []))
        for f, position in kinks.get(weakest, []):
            attempt.resign[(f, position)] = -1 if pressures[weakest] > 0.0 else 1

    # A limit's entry the solve carried across its center no longer has the sign
    # its row assumed: it goes to the kink, from where the next solve can release
    # it to whichever side the gradient asks for.
    for f, signs in families.items():
        for position, sign in enumerate(signs):
            entry = layout.family_start[f] + position
            offset = x[layout.entry_coordinate[entry]] - layout.entry_center[entry]
            if sign != 0 and sign * offset < -feasibility_tolerance:
                attempt.resign[(f, position)] = 0

    # A violated constraint joins.
    values = _row_values(layout, x, [0.0] * layout.entry_count)
    for row in range(layout.simple_count):
        if (
            row not in binding.simple
            and values[row] - layout.simple_b[row] < -feasibility_tolerance
        ):
            attempt.add_simple.add(row)
    base = layout.simple_count
    for g in range(layout.general_count):
        if (
            g not in binding.general
            and values[base + g] - layout.general_b[g] < -feasibility_tolerance
        ):
            attempt.add_general.add(g)
    for f, family in enumerate(program.absolute_sums):
        if f in families:
            continue
        total = math.fsum(
            abs(x[index] - center)
            for index, center in zip(family.indices, family.centers, strict=True)
        )
        if total - family.limit > feasibility_tolerance:
            attempt.add_families[f] = [
                0 if x[index] == center else (1 if x[index] > center else -1)
                for index, center in zip(family.indices, family.centers, strict=True)
            ]
    return attempt if attempt.changes else None


def _nearest_multipliers(
    scaled: _Scaled,
    it: _Iterate,
    x: Sequence[float],
    fixed: dict[int, float],
    kinks: dict[int, list[tuple[int, int]]],
    rows: Sequence[tuple[str, list[int], list[float], float, bool]],
    row_kind: Sequence[tuple[str, int]],
    reduced_rows: Sequence[int],
    schur: Sequence[Sequence[float]],
    factor: _Cholesky,
    particular: list[float],
) -> list[float]:
    """Of the multipliers dependent binding rows allow, the ones nearest the interior point's.

    Binding rows that are dependent over the free variables -- a degenerate
    vertex -- leave the multipliers a family rather than a point: ``particular``
    plus any combination of the null vectors the skipped rows define. The
    interior point estimates every multiplier, and its estimate lies inside the
    family, away from the corners where one changes sign. This takes the member
    that matches it best in the least-squares sense, over both what the rows
    carry and what each fixed variable's bounds and kinks must then carry. The
    point ``x`` does not change.
    """

    layout = scaled.layout
    count = len(particular)
    above, below = layout.base_above, layout.base_below
    estimate: list[float] = []
    for number in reduced_rows:
        kind, which = row_kind[number]
        if kind == "equality":
            estimate.append(it.y[which] if it.y else 0.0)
        elif kind == "general":
            estimate.append(it.z[layout.simple_count + which] if it.z else 0.0)
        else:
            estimate.append(it.z[layout.base_sum + which] if it.z else 0.0)
    null: list[list[float]] = []
    for row in (row for row, flag in enumerate(factor.skipped) if flag):
        combination = factor.solve([schur[other][row] for other in range(count)])
        vector = [-value for value in combination]
        vector[row] += 1.0
        null.append(vector)

    # What each fixed variable's bounds and kinks carry: the gradient less the
    # rows, now; and as the interior point had it.
    gradient = scaled.gradient(x)
    carried = {index: gradient[index] for index in fixed}
    on_fixed: list[list[tuple[int, float]]] = []
    for number, multiplier in zip(reduced_rows, particular, strict=True):
        _, indices, coefficients, _, _ = rows[number]
        terms = [(i, c) for i, c in zip(indices, coefficients, strict=True) if i in fixed]
        on_fixed.append(terms)
        for index, coefficient in terms:
            carried[index] -= multiplier * coefficient
    # A limit's entry that is not at its kink is in the limit's row, whose
    # multiplier is among the rows'; only a kink's share is carried here.
    carried_estimate = dict.fromkeys(fixed, 0.0)
    if it.z:
        for row, (index, q) in enumerate(zip(layout.simple_index, layout.simple_q, strict=True)):
            if index in carried_estimate:
                carried_estimate[index] += it.z[row] * q
        for index, entries in kinks.items():
            for f, position in entries:
                entry = layout.family_start[f] + position
                carried_estimate[index] += it.z[below + entry] - it.z[above + entry]

    # Least squares in the null-vector weights: one equation per row multiplier
    # and one per fixed variable.
    effects: list[dict[int, float]] = []
    for vector in null:
        effect: dict[int, float] = {}
        for weight, terms in zip(vector, on_fixed, strict=True):
            if weight != 0.0:
                for index, coefficient in terms:
                    effect[index] = effect.get(index, 0.0) - weight * coefficient
        effects.append(effect)
    design = [[vector[row] for vector in null] for row in range(count)]
    target = [goal - value for goal, value in zip(estimate, particular, strict=True)]
    for index in sorted(fixed):
        design.append([effect.get(index, 0.0) for effect in effects])
        target.append(carried_estimate[index] - carried[index])
    width = len(null)
    gram = [
        [math.fsum(line[a] * line[b] for line in design) for b in range(width)]
        for a in range(width)
    ]
    weights = _Cholesky.of(gram).solve(
        [math.fsum(line[a] * goal for line, goal in zip(design, target, strict=True))
         for a in range(width)]
    )  # fmt: skip
    result = list(particular)
    for weight, vector in zip(weights, null, strict=True):
        result = [value + weight * step for value, step in zip(result, vector, strict=True)]
    return result


def _finish(
    scaled: _Scaled,
    it: _Iterate,
    feasibility_tolerance: float,
    convergence_tolerance: float,
) -> tuple[_Finished | None, int]:
    """Identify the binding set, solve exactly, correct it a bounded number of times."""

    binding = _identify(scaled, it)
    seen: set[tuple[object, ...]] = set()
    rounds = 0
    while rounds < _FINISH_ROUNDS:
        signature = binding.signature()
        if signature in seen:
            return None, rounds
        seen.add(signature)
        rounds += 1
        attempt = _exact(scaled, binding, it, feasibility_tolerance, convergence_tolerance)
        if attempt is None:
            return None, rounds
        if attempt.finished is not None:
            return attempt.finished, rounds
        binding.simple = (binding.simple - attempt.drop_simple) | attempt.add_simple
        binding.general = (binding.general - attempt.drop_general) | attempt.add_general
        for (f, position), sign in attempt.resign.items():
            if f in binding.families:
                binding.families[f][position] = sign
        for f in attempt.drop_families:
            binding.families.pop(f, None)
        binding.families.update(attempt.add_families)
    return None, rounds


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #


def solve_factor_quadratic_program(
    program: FactorQuadraticProgram,
    *,
    feasibility_tolerance: float,
    convergence_tolerance: float,
    max_iterations: int,
) -> QuadraticSolution:
    """Solve ``program`` and certify the answer. See the module docstring.

    ``iterations`` in the result counts interior-point iterations and the exact
    finish's solves, and is what ``max_iterations`` bounds.

    Raises:
        OptimizationError: If a tolerance is not a positive finite number below
            one, or ``max_iterations`` is not a positive integer. A program this
            method cannot certify is a status, not an exception.
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

    scaled = _Scaled(program)
    layout = scaled.layout
    pivot_ratio = program.curvature.pivot_ratio
    used = 0

    def certified(finished: _Finished, steps: int) -> QuadraticSolution:
        gradient = program.curvature.times(finished.x)
        objective = 0.5 * math.fsum(
            value * product for value, product in zip(finished.x, gradient, strict=True)
        ) + math.fsum(
            linear * value for linear, value in zip(program.linear, finished.x, strict=True)
        )
        return QuadraticSolution(
            SolveStatus.OPTIMAL,
            tuple(finished.x),
            objective,
            steps,
            finished.active,
            MappingProxyType(finished.multipliers),
            (),
            finished.violation,
            finished.stationarity,
            pivot_ratio,
            f"Optimal: an interior point ({steps} steps) finished exactly on the binding "
            "constraints; no constraint is violated and the KKT certificate verifies.",
        )

    def unfinished(status: SolveStatus, steps: int, detail: str) -> QuadraticSolution:
        return QuadraticSolution(
            status,
            None,
            None,
            steps,
            (),
            MappingProxyType({}),
            (),
            0.0,
            math.inf,
            pivot_ratio,
            detail,
        )

    if layout.inequality_count == 0:
        finished, rounds = _finish(
            scaled,
            _Iterate([0.0] * layout.size, [], [0.0] * len(layout.equality_b), [], []),
            feasibility_tolerance,
            convergence_tolerance,
        )
        if finished is not None:
            return certified(finished, rounds)
        return unfinished(
            SolveStatus.NUMERICAL_FAILURE,
            rounds,
            "The equality-constrained program did not certify: its equalities are inconsistent "
            "or too ill-conditioned for double precision.",
        )

    it = _start(scaled)
    count = layout.inequality_count
    attempted_at = math.inf
    limit = min(max_iterations, _INTERIOR_LIMIT)
    best = math.inf
    stale = 0
    stalled = (
        "The interior point stopped making progress -- what inconsistent constraints look like "
        "to it. It does not prove inconsistency; the dense method does."
    )
    while True:
        residuals = _residuals(scaled, it)
        rx, rt, rp, re = residuals
        mu = math.fsum(sj * zj for sj, zj in zip(it.s, it.z, strict=True)) / count
        primal = max(
            max((abs(value) for value in rp), default=0.0),
            max((abs(value) for value in re), default=0.0),
        )
        dual = max(
            max((abs(value) for value in rx), default=0.0),
            max((abs(value) for value in rt), default=0.0),
        )
        merit = max(primal, dual, mu)
        if merit < 0.5 * best:
            best, stale = merit, 0
        else:
            stale += 1
        if merit <= _FINISH_FROM and mu <= attempted_at / 10.0:
            attempted_at = mu
            finished, rounds = _finish(scaled, it, feasibility_tolerance, convergence_tolerance)
            used += rounds
            if finished is not None:
                return certified(finished, used)
        if used >= max_iterations:
            return unfinished(
                SolveStatus.ITERATION_LIMIT,
                used,
                f"The interior point did not finish within {max_iterations} steps.",
            )
        if stale >= _STALL_ITERATIONS:
            return unfinished(SolveStatus.NUMERICAL_FAILURE, used, stalled)
        if used >= limit or mu <= _COMPLEMENTARITY_FLOOR:
            return unfinished(
                SolveStatus.NUMERICAL_FAILURE,
                used,
                "The interior point converged as far as double precision allows, and no exact "
                "finish on the constraints it found binding certified.",
            )

        newton = _Newton(scaled, it)
        products = [sj * zj for sj, zj in zip(it.s, it.z, strict=True)]
        affine = _direction(scaled, it, newton, residuals, products)
        alpha = min(_longest(it.s, affine[4]), _longest(it.z, affine[3]))
        mu_affine = (
            math.fsum(
                (sj + alpha * dsj) * (zj + alpha * dzj)
                for sj, dsj, zj, dzj in zip(it.s, affine[4], it.z, affine[3], strict=True)
            )
            / count
        )
        centering = (mu_affine / mu) ** 3 if mu > 0.0 else 0.0
        target = [
            product + dsj * dzj - centering * mu
            for product, dsj, dzj in zip(products, affine[4], affine[3], strict=True)
        ]
        dx, dt, dy, dz, ds = _direction(scaled, it, newton, residuals, target)
        alpha = min(1.0, _STEP_FRACTION * min(_longest(it.s, ds), _longest(it.z, dz)))
        if alpha < _SHORTEST_STEP:
            return unfinished(SolveStatus.NUMERICAL_FAILURE, used + 1, stalled)
        it = _Iterate(
            [a + alpha * b for a, b in zip(it.x, dx, strict=True)],
            [a + alpha * b for a, b in zip(it.t, dt, strict=True)],
            [a + alpha * b for a, b in zip(it.y, dy, strict=True)],
            [a + alpha * b for a, b in zip(it.z, dz, strict=True)],
            [a + alpha * b for a, b in zip(it.s, ds, strict=True)],
        )
        used += 1
