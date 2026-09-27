"""Risk budgeting by cyclical coordinate descent: the long-only portfolio whose
risk contributions are stated shares of its volatility.

What is solved
--------------

For a positive definite covariance ``C`` and budgets ``b_i > 0`` summing to one,
there is exactly one long-only, fully invested portfolio whose Euler risk
contributions are those shares of its volatility:

.. code-block:: text

    w_i * (C w)_i / (w' C w) = b_i        for every i,   w > 0,   sum w = 1

Equal budgets give the *equal risk contribution* ("risk parity") portfolio.
Uniqueness, and the method, come from one change of variables (Spinu 2013;
Roncalli, *Introduction to Risk Parity and Budgeting*, 2013): the strictly
convex problem

.. code-block:: text

    minimize  1/2 y' C y - sum_i b_i ln(y_i)       over y > 0

has a unique minimizer, whose first-order conditions are ``y_i (C y)_i = b_i``.
Scaling ``w = y / sum(y)`` preserves every ratio, and summing the conditions
gives ``y' C y = 1``, so ``w`` has exactly the stated shares.

The method
----------

Cyclical coordinate descent (Griveau-Billion, Richard and Roncalli, 2013).
Holding every other coordinate fixed, the condition for ``y_i`` is the
quadratic ``C_ii y_i^2 + a_i y_i - b_i = 0`` with ``a_i = sum_{j != i} C_ij y_j``,
whose one positive root is

.. code-block:: text

    y_i = (-a_i + sqrt(a_i^2 + 4 C_ii b_i)) / (2 C_ii)

Each update solves its coordinate's subproblem exactly, so the objective
decreases monotonically and, being strictly convex with a unique minimizer, the
iteration converges to it. ``C y`` is kept current incrementally, so a sweep
costs ``O(n^2)``. Only additions, multiplications, divisions and square roots
are used -- every one correctly rounded under IEEE-754 -- so the result is the
same bits on every platform.

Convergence, stated
-------------------

After every sweep the relative contributions of ``w = y / sum(y)`` are measured
directly, and the iteration stops when the largest deviation from its budget,
``max_i |w_i (C w)_i / (w' C w) - b_i|``, is at most the convergence
tolerance. That is the quantity a caller cares about, measured, rather than a
step size. ``max_sweeps`` sweeps without reaching it is non-convergence, and the
caller is told so rather than handed the last iterate.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from alphalab.portfolio_optimizer.exceptions import OptimizationError

__all__ = ["RiskParityIterate", "solve_risk_budgets"]


@dataclass(frozen=True, slots=True)
class RiskParityIterate:
    """Where coordinate descent stopped.

    Attributes:
        weights: ``y / sum(y)``, in the order the budgets were given. Long-only
            and summing to one.
        converged: Whether the largest budget deviation reached the tolerance.
        sweeps: Full passes over the coordinates taken.
        max_deviation: ``max_i |relative contribution_i - b_i|`` at the end.
    """

    weights: tuple[float, ...]
    converged: bool
    sweeps: int
    max_deviation: float


def _relative_contributions(
    weights: Sequence[float], rows: Sequence[Sequence[float]]
) -> tuple[float, ...]:
    count = len(weights)
    exposures = [
        math.fsum(rows[index][other] * weights[other] for other in range(count))
        for index in range(count)
    ]
    variance = math.fsum(weights[index] * exposures[index] for index in range(count))
    return tuple(weights[index] * exposures[index] / variance for index in range(count))


def solve_risk_budgets(
    rows: Sequence[Sequence[float]],
    budgets: Sequence[float],
    *,
    convergence_tolerance: float,
    max_sweeps: int,
) -> RiskParityIterate:
    """Solve for the long-only portfolio whose risk shares are ``budgets``.

    ``rows`` is a positive definite covariance, aligned with ``budgets``. The
    caller has already checked definiteness (a zero variance makes a positive
    budget unreachable at any finite weight, and a singular matrix can leave the
    change of variables without a minimizer).

    Raises:
        OptimizationError: If a budget is not positive, the budgets do not sum
            to one within ``count * eps``, or a variance is not positive.
    """

    count = len(budgets)
    if count == 0 or len(rows) != count:
        raise OptimizationError("Risk budgets and the covariance must name the same assets.")
    if any(budget <= 0.0 for budget in budgets):
        raise OptimizationError(
            "Every risk budget must be positive: a zero budget is met only by a zero weight, "
            "which the change of variables cannot reach."
        )
    total = math.fsum(budgets)
    if abs(total - 1.0) > count * sys.float_info.epsilon:
        raise OptimizationError(f"Risk budgets sum to {total!r}, not one.")
    diagonal = [rows[index][index] for index in range(count)]
    if any(value <= 0.0 for value in diagonal):
        raise OptimizationError("Risk budgeting needs a positive variance for every asset.")

    y = [budgets[index] / math.sqrt(diagonal[index]) for index in range(count)]
    exposure = [
        math.fsum(rows[index][other] * y[other] for other in range(count)) for index in range(count)
    ]

    deviation = math.inf
    sweeps = 0
    while sweeps < max_sweeps:
        for index in range(count):
            cross = exposure[index] - diagonal[index] * y[index]
            updated = (
                -cross + math.sqrt(cross * cross + 4.0 * diagonal[index] * budgets[index])
            ) / (2.0 * diagonal[index])
            change = updated - y[index]
            if change != 0.0:
                y[index] = updated
                for other in range(count):
                    exposure[other] += rows[other][index] * change
        sweeps += 1
        # Recomputed from scratch each sweep rather than read from the running
        # exposures, so that incremental drift can never reach the stopping test.
        scale = math.fsum(y)
        weights = tuple(value / scale for value in y)
        shares = _relative_contributions(weights, rows)
        deviation = max(abs(share - budget) for share, budget in zip(shares, budgets, strict=True))
        if deviation <= convergence_tolerance:
            return RiskParityIterate(weights, True, sweeps, deviation)

    scale = math.fsum(y)
    return RiskParityIterate(tuple(value / scale for value in y), False, sweeps, deviation)
