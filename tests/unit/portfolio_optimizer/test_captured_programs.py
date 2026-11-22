"""Two programs a ten-year rebalancing backtest met, each certified (v4.3, ADR-0063, TD-76).

Example 83's tight trial rebalances monthly to the most expected return a
value-neutral, sector-capped book holds with the CVaR of its daily loss at most
1.2%. Of its 114 constructions, two did not trade, and each is held here
exactly as the model stated it (``_captured_programs``):

* **The 105th is feasible.** The least CVaR the other constraints allow is
  0.0092, inside the limit, yet the linear program returned
  ``NUMERICAL_FAILURE``: from its 21st iteration the Newton direction stopped
  meeting the primal equations. The normal matrix's lift was ``1e-12`` of its
  largest diagonal entry, which swamped the scenario-excess rows -- their own
  entries were many orders smaller near the optimum -- and the error gathered
  in the CVaR row that sums them. A near-full step put it into a row already
  met, the row's variables were pressed to their bounds, and the iterate
  jammed at a residual of 4.755e-07 whatever the budget. Scaled to a unit
  diagonal before the lift, the program is certified at a vertex.
* **The 107th is infeasible**, and is proven so: its least CVaR, solved
  separately, exceeds the limit by exactly the margin the Farkas certificate
  states.

Each answer is checked here without the solver: the weights against every
constraint, and the CVaR by sorting the scenarios' losses.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any

import pytest

from alphalab.analytics.risk_model import Classification, CovarianceMatrix, FactorLoadings
from alphalab.portfolio_optimizer import (
    ConstraintSet,
    ConstructionProblem,
    ConstructionStatus,
    CVaRLimit,
    ExpectedReturns,
    ExposureRange,
    FactorBound,
    GroupBound,
    LinearCosts,
    MaximumReturn,
    MinimumCVaR,
    ScenarioSet,
    SolverSettings,
    WeightBounds,
    construct,
)
from tests.unit.portfolio_optimizer._captured_programs import CAPTURED

TOLERANCE = 1e-9


def _problem(captured: Mapping[str, Any]) -> ConstructionProblem:
    """The program as example 83's model states it, its sources word for word."""

    assets = tuple(captured["assets"])
    scenarios = ScenarioSet(
        assets,
        tuple(tuple(row) for row in captured["scenarios"]),
        currency="USD",
        period="1D",
        source="the last 63 daily returns, as they happened",
    )
    sectors = Classification(
        "sector", captured["sectors"], source="the instrument registry", as_of=None
    )
    loadings = FactorLoadings(
        ("value",),
        assets,
        tuple((captured["loadings"][asset],) for asset in assets),
        source="example 83's declared value scores",
        lineage=MappingProxyType({"value": "book-to-price scores, standardized, as declared"}),
        as_of=None,
    )
    constraints = ConstraintSet(
        ExposureRange.exactly(captured["invested"]),
        WeightBounds(0.0, captured["weight_cap"]),
        groups=tuple(
            GroupBound(sectors, sector, None, captured["sector_cap"])
            for sector in sorted(set(captured["sectors"].values()))
        ),
        factors=(FactorBound.neutral(loadings, "value"),),
        limits=(CVaRLimit(scenarios, captured["level"], captured["limit"]),),
    )
    objective = MaximumReturn(
        ExpectedReturns(
            captured["expected"],
            currency="USD",
            period="1D",
            source="mean daily return over the last 126 days",
        ),
        LinearCosts(captured["current"], captured["rates"]),
    )
    covariance = CovarianceMatrix.from_rows(
        assets,
        [list(row) for row in captured["covariance"]],
        currency="USD",
        period="1D",
        source="sample",
        observations=len(captured["scenarios"]),
    )
    return ConstructionProblem(covariance, objective, constraints, SolverSettings(1e-9, 1e-9, 500))


def _least_cvar(problem: ConstructionProblem) -> tuple[float, Mapping[str, float]]:
    """The least CVaR the program's other constraints allow, solved as its own program."""

    limit = problem.constraints.limits[0]
    assert isinstance(limit, CVaRLimit)
    least = construct(
        replace(
            problem,
            objective=MinimumCVaR(limit.scenarios, limit.level),
            constraints=replace(problem.constraints, limits=()),
        )
    )
    assert least.status is ConstructionStatus.OPTIMAL and least.weights is not None
    value = least.diagnostics.objective_value
    assert value is not None
    return value, least.weights


def _net_return(captured: Mapping[str, Any], weights: Mapping[str, float]) -> float:
    """Expected return less costs, evaluated directly: what maximum return maximizes."""

    return math.fsum(
        captured["expected"][asset] * weights[asset]
        - captured["rates"][asset] * abs(weights[asset] - captured["current"][asset])
        for asset in captured["assets"]
    )


def _sorted_cvar(
    scenarios: tuple[tuple[float, ...], ...], weights: list[float], level: float
) -> float:
    """The CVaR of the loss at ``level``, by sorting: the worst ``(1 - level) S`` losses, averaged.

    The scenario at the quantile counts for the fraction of it the tail needs.
    """

    losses = sorted(
        (-math.fsum(r * w for r, w in zip(row, weights, strict=True)) for row in scenarios),
        reverse=True,
    )
    tail = (1.0 - level) * len(losses)
    whole = math.floor(tail)
    total = math.fsum(losses[:whole])
    if tail > whole:
        total += (tail - whole) * losses[whole]
    return total / tail


@pytest.mark.parametrize("number", sorted(CAPTURED))
def test_each_captured_program_is_the_one_its_model_stated(number: int) -> None:
    assert _problem(CAPTURED[number]).problem_id == CAPTURED[number]["problem_id"]


def test_the_program_whose_interior_point_jammed_is_certified() -> None:
    captured = CAPTURED[105]
    problem = _problem(captured)
    result = construct(problem)
    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    assert result.weights is not None
    assets = tuple(captured["assets"])
    weights = [result.weights[asset] for asset in assets]
    # Every constraint, evaluated without the solver.
    assert math.fsum(weights) == pytest.approx(captured["invested"], abs=TOLERANCE)
    assert all(-TOLERANCE <= w <= captured["weight_cap"] + TOLERANCE for w in weights)
    for sector in set(captured["sectors"].values()):
        held = math.fsum(
            w for a, w in zip(assets, weights, strict=True) if captured["sectors"][a] == sector
        )
        assert held <= captured["sector_cap"] + TOLERANCE
    exposure = math.fsum(captured["loadings"][a] * w for a, w in zip(assets, weights, strict=True))
    assert abs(exposure) <= TOLERANCE
    cvar = _sorted_cvar(captured["scenarios"], weights, captured["level"])
    assert cvar <= captured["limit"] + TOLERANCE
    # It is feasible with room: the least-CVaR portfolio is well inside the limit,
    # and the answer is at least as good as that portfolio.
    least, portfolio = _least_cvar(problem)
    assert least < captured["limit"] - 1e-3
    assert _net_return(captured, result.weights) >= _net_return(captured, portfolio) - TOLERANCE
    assert result.diagnostics.objective_value == pytest.approx(
        _net_return(captured, result.weights), abs=TOLERANCE
    )


def test_the_program_that_cannot_hold_its_limit_is_proven_infeasible_by_its_own_margin() -> None:
    captured = CAPTURED[107]
    problem = _problem(captured)
    result = construct(problem)
    assert result.status is ConstructionStatus.INFEASIBLE
    assert result.weights is None
    # The least CVaR, solved separately, misses the limit by the margin the
    # certificate states: the program's least total violation.
    least, _ = _least_cvar(problem)
    miss = least - captured["limit"]
    assert miss > 0.0
    assert f"by {miss:.6g}" in result.diagnostics.detail
