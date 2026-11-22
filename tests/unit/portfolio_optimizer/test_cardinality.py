"""The cardinality relaxation: two certified steps, the gap to the best stated (ADR-0063).

* **Against every subset.** Over five assets, the best portfolio holding at
  most ``k`` names is found by solving the problem restricted to each of the
  ``C(5, k)`` sets of names. The relaxed optimum is never worse than it, the
  answer never better, and the stated gap always covers the distance between
  them -- for minimum variance, mean-variance, maximum return and minimum CVaR.
* **The outcomes.** An optimum that already keeps the limit is the answer,
  exactly; the names are the largest, ties broken by asset id, with every name
  the bounds hold away from zero; and no certified portfolio is claimed when
  the relaxation fails, the bounds hold too many names, or the restriction is
  infeasible.
* **Objective values**, each in its objective's own sense.
* **Identity and refusals.**
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from dataclasses import replace

import pytest

from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.portfolio_optimizer import (
    BoxUncertainty,
    CardinalityOutcome,
    ConstraintSet,
    ConstructionProblem,
    ConstructionStatus,
    ExpectedReturns,
    ExposureRange,
    LinearCosts,
    MaximumDiversification,
    MaximumReturn,
    MeanVariance,
    MinimumCVaR,
    MinimumVariance,
    RiskParity,
    RobustMeanVariance,
    ScenarioSet,
    SolverSettings,
    WeightBounds,
    construct,
    construct_with_cardinality,
)
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError
from tests.unit.portfolio_optimizer.test_linear_objectives import ScenarioCVaR
from tests.unit.portfolio_optimizer.test_objective_protocol import TrackingError

ASSETS = ("A", "B", "C", "D", "E")
ROWS = (
    (0.040, 0.012, 0.006, 0.010, 0.004),
    (0.012, 0.090, 0.018, 0.015, 0.009),
    (0.006, 0.018, 0.060, 0.012, 0.010),
    (0.010, 0.015, 0.012, 0.050, 0.008),
    (0.004, 0.009, 0.010, 0.008, 0.030),
)
SETTINGS = SolverSettings(1e-10, 1e-10, 500)
FULL = ExposureRange.exactly(1.0)
MU = ExpectedReturns(
    {"A": 0.010, "B": 0.022, "C": 0.017, "D": 0.012, "E": 0.008}, "USD", "1M", "unit"
)
SCENARIOS = ScenarioSet.of(
    [
        {"A": 0.0625, "B": -0.125, "C": 0.03125, "D": 0.015625, "E": -0.03125},
        {"A": -0.09375, "B": 0.0625, "C": 0.015625, "D": -0.0625, "E": 0.03125},
        {"A": 0.03125, "B": 0.09375, "C": -0.0625, "D": 0.0, "E": -0.015625},
        {"A": -0.03125, "B": -0.046875, "C": 0.078125, "D": 0.03125, "E": 0.0},
        {"A": 0.015625, "B": 0.03125, "C": -0.109375, "D": -0.046875, "E": 0.046875},
        {"A": -0.0625, "B": 0.0, "C": 0.046875, "D": 0.0625, "E": -0.0625},
        {"A": 0.078125, "B": -0.015625, "C": -0.03125, "D": -0.015625, "E": 0.015625},
        {"A": 0.0, "B": 0.046875, "C": 0.0625, "D": -0.03125, "E": 0.03125},
    ],
    currency="USD",
    period="1M",
    source="dyadic unit scenarios",
)


def covariance() -> CovarianceMatrix:
    return CovarianceMatrix.from_rows(
        ASSETS, ROWS, currency="USD", period="1M", source="unit", observations=None
    )


def problem(objective: object, constraints: ConstraintSet) -> ConstructionProblem:
    return ConstructionProblem(covariance(), objective, constraints, SETTINGS)  # type: ignore[arg-type]


def capped(cap: float) -> ConstraintSet:
    return ConstraintSet(FULL, WeightBounds(0.0, cap))


def held(weights: Mapping[str, float]) -> int:
    return sum(abs(value) > SETTINGS.feasibility_tolerance for value in weights.values())


def best_over_subsets(stated: ConstructionProblem, k: int, sense: float) -> float | None:
    """The best value of any portfolio holding at most ``k`` names: every set of ``k``, solved.

    ``None`` when no set of ``k`` names can meet the constraints.
    """

    values = []
    for names in itertools.combinations(ASSETS, k):
        bounds = stated.constraints.bounds
        for asset in ASSETS:
            if asset not in names:
                bounds = bounds.with_asset(asset, 0.0, 0.0)
        result = construct(replace(stated, constraints=replace(stated.constraints, bounds=bounds)))
        if result.succeeded:
            value = result.diagnostics.objective_value
            assert value is not None
            values.append(value)
    if not values:
        return None
    return min(values) if sense > 0 else max(values)


CASES = [
    ("minimum-variance", MinimumVariance(), capped(1.0), 1.0),
    ("mean-variance", MeanVariance(MU, 4.0), capped(1.0), -1.0),
    # Costs from an equal book keep four names under caps of 0.4: two cannot hold a
    # full book, three can, four keep the limit already.
    (
        "maximum-return",
        MaximumReturn(MU, LinearCosts(dict.fromkeys(ASSETS, 0.2), dict.fromkeys(ASSETS, 0.004))),
        capped(0.4),
        -1.0,
    ),
    ("minimum-cvar", MinimumCVaR(SCENARIOS, 0.75), capped(1.0), 1.0),
]


@pytest.mark.parametrize(
    ("name", "objective", "constraints", "sense"), CASES, ids=[c[0] for c in CASES]
)
@pytest.mark.parametrize("k", [2, 3, 4])
def test_the_bound_and_the_answer_bracket_the_best_over_every_subset(
    name: str, objective: object, constraints: ConstraintSet, sense: float, k: int
) -> None:
    stated = problem(objective, constraints)
    result = construct_with_cardinality(stated, k)
    best = best_over_subsets(stated, k, sense)
    if result.weights is None:
        # Honest: no set of k names is feasible, or the one chosen is not.
        assert result.outcome is CardinalityOutcome.UNRESOLVED
        assert best is None or (
            result.restricted is not None
            and result.restricted.status is ConstructionStatus.INFEASIBLE
        )
        return
    assert best is not None
    assert held(result.weights) <= k
    bound = result.bound
    answer = (
        result.relaxed if result.restricted is None else result.restricted
    ).diagnostics.objective_value
    assert bound is not None and answer is not None and result.gap is not None
    slack = 1e-9
    # The relaxation is never worse than the best, the answer never better, and
    # the gap covers the distance from the answer to the best.
    assert sense * (best - bound) >= -slack
    assert sense * (answer - best) >= -slack
    assert result.gap >= sense * (answer - best) - slack


def test_the_cases_reach_every_outcome() -> None:
    outcomes = {
        construct_with_cardinality(problem(objective, constraints), k).outcome
        for _, objective, constraints, _ in CASES
        for k in (2, 3, 4)
    }
    assert outcomes == set(CardinalityOutcome)


def test_an_optimum_that_keeps_the_limit_is_the_answer_exactly() -> None:
    # Maximum return under caps of 0.5 holds two names, B and C.
    stated = problem(MaximumReturn(MU), capped(0.5))
    result = construct_with_cardinality(stated, 3)
    assert result.outcome is CardinalityOutcome.WITHIN and result.restricted is None
    assert result.weights == construct(stated).weights and result.gap == 0.0
    assert result.names == ("B", "C")


def test_the_names_are_the_largest_ties_broken_by_asset_id() -> None:
    # Five identical, independent assets: the minimum-variance weights tie at 0.2.
    identical = CovarianceMatrix.from_rows(
        ASSETS,
        [[0.04 if i == j else 0.0 for j in range(5)] for i in range(5)],
        currency="USD",
        period="1M",
        source="unit",
        observations=None,
    )
    stated = ConstructionProblem(identical, MinimumVariance(), capped(1.0), SETTINGS)
    result = construct_with_cardinality(stated, 2)
    assert result.outcome is CardinalityOutcome.RESTRICTED
    assert result.names == ("A", "B")
    assert result.weights is not None
    assert dict(result.weights) == pytest.approx(
        {"A": 0.5, "B": 0.5, "C": 0.0, "D": 0.0, "E": 0.0}, abs=1e-12
    )


def test_a_name_the_bounds_hold_away_from_zero_is_kept() -> None:
    # E's lower bound keeps it held, though the relaxed optimum holds it least.
    bounds = WeightBounds(0.0, 1.0).with_asset("E", 0.05, 1.0)
    result = construct_with_cardinality(
        problem(MeanVariance(MU, 4.0), ConstraintSet(FULL, bounds)), 2
    )
    assert "E" in result.names and len(result.names) == 2
    assert result.weights is not None and result.weights["E"] >= 0.05 - 1e-12


@pytest.mark.parametrize(
    ("constraints", "k", "reason"),
    [
        (capped(0.1), 2, "without the limit ended INFEASIBLE"),
        (
            ConstraintSet(
                FULL, WeightBounds(0.0, 1.0).with_asset("A", 0.1, 1.0).with_asset("B", 0.1, 1.0)
            ),
            1,
            "more than 1",
        ),
        (capped(0.3), 3, "restricted to"),
    ],
    ids=["relaxation-infeasible", "too-many-kept", "restriction-infeasible"],
)
def test_no_certified_portfolio_is_claimed_when_none_keeps_the_limit(
    constraints: ConstraintSet, k: int, reason: str
) -> None:
    # Three names capped at 0.3 cannot hold all of a fully invested book.
    result = construct_with_cardinality(problem(MinimumVariance(), constraints), k)
    assert result.outcome is CardinalityOutcome.UNRESOLVED
    assert result.weights is None and result.gap is None and reason in result.detail
    if reason == "restricted to":
        assert result.restricted is not None
        assert result.restricted.status is ConstructionStatus.INFEASIBLE


# --------------------------------------------------------------------------- #
# Objective values
# --------------------------------------------------------------------------- #


def _dot(weights: Mapping[str, float], values: Mapping[str, float]) -> float:
    return sum(weights[a] * values[a] for a in ASSETS)


def test_each_objective_reports_its_value_in_its_own_sense() -> None:
    minimum = construct(problem(MinimumVariance(), capped(1.0)))
    assert minimum.diagnostics.objective_value == minimum.diagnostics.variance
    mean = construct(problem(MeanVariance(MU, 4.0), capped(1.0)))
    weights = mean.require_weights()
    variance = mean.diagnostics.variance
    assert variance is not None
    assert mean.diagnostics.objective_value == pytest.approx(
        _dot(weights, MU.values) - 2.0 * variance, abs=1e-15
    )
    book = dict.fromkeys(ASSETS, 0.2)
    costs = LinearCosts(book, dict.fromkeys(ASSETS, 0.001))
    priced = construct(problem(MaximumReturn(MU, costs), capped(0.4)))
    weights = priced.require_weights()
    assert priced.diagnostics.objective_value == pytest.approx(
        _dot(weights, MU.values) - costs.cost(weights), abs=1e-15
    )
    cvar = construct(problem(MinimumCVaR(SCENARIOS, 0.75), capped(1.0)))
    assert cvar.diagnostics.objective_value == SCENARIOS.cvar(cvar.require_weights(), 0.75)
    diversified = construct(problem(MaximumDiversification(), capped(1.0)))
    assert diversified.diagnostics.objective_value == diversified.diagnostics.diversification_ratio
    robust = construct(
        problem(
            RobustMeanVariance(MU, 4.0, BoxUncertainty(dict.fromkeys(ASSETS, 0.002))), capped(1.0)
        )
    )
    worst, risk = robust.diagnostics.worst_case_return, robust.diagnostics.variance
    assert worst is not None and risk is not None
    assert robust.diagnostics.objective_value == pytest.approx(worst - 2.0 * risk, abs=1e-15)
    assert construct(problem(RiskParity.equal(), capped(1.0))).diagnostics.objective_value is None


def test_an_objective_of_the_protocol_reports_its_forms_value() -> None:
    benchmark = {"A": 0.3, "B": 0.1, "C": 0.2, "D": 0.2, "E": 0.2}
    tracked = construct(
        problem(TrackingError(benchmark), ConstraintSet(FULL, WeightBounds(0.0, 0.25)))
    )
    weights = tracked.require_weights()
    difference = {a: weights[a] - benchmark[a] for a in ASSETS}
    matrix = dict(zip(ASSETS, ROWS, strict=True))
    tracking = sum(
        difference[a] * matrix[a][j] * difference[b] for a in ASSETS for j, b in enumerate(ASSETS)
    )
    constant = sum(
        benchmark[a] * matrix[a][j] * benchmark[b] for a in ASSETS for j, b in enumerate(ASSETS)
    )
    # The form drops the benchmark's own variance, a constant.
    assert tracked.diagnostics.objective_value == pytest.approx(tracking - constant, abs=1e-12)
    lifted = construct(
        problem(
            ScenarioCVaR(
                tuple(dict(zip(SCENARIOS.assets, row, strict=True)) for row in SCENARIOS.returns),
                0.75,
                1.0,
            ),
            capped(1.0),
        )
    )
    assert lifted.diagnostics.objective_value == pytest.approx(
        SCENARIOS.cvar(lifted.require_weights(), 0.75), abs=1e-9
    )


# --------------------------------------------------------------------------- #
# Identity and refusals
# --------------------------------------------------------------------------- #


def test_the_limited_problem_is_identified_by_the_problem_and_the_limit() -> None:
    stated = problem(MeanVariance(MU, 4.0), capped(1.0))
    first = construct_with_cardinality(stated, 2)
    again = construct_with_cardinality(stated, 2)
    other = construct_with_cardinality(stated, 3)
    assert first.problem_id == again.problem_id and first.result_id == again.result_id
    assert first.problem_id != other.problem_id
    assert first.relaxed.problem_id == stated.problem_id


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_a_limit_that_is_not_a_positive_integer_is_refused(k: object) -> None:
    with pytest.raises(ConstructionInputError, match="positive integer"):
        construct_with_cardinality(problem(MinimumVariance(), capped(1.0)), k)  # type: ignore[arg-type]


def test_risk_parity_is_refused() -> None:
    with pytest.raises(ConstructionInputError, match="budgets over the whole universe"):
        construct_with_cardinality(problem(RiskParity.equal(), capped(1.0)), 2)
