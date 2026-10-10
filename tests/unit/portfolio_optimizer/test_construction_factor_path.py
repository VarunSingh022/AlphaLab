"""Construction through a covariance's factor structure (v3.12, ledger PRF-005).

Every comparison here solves one problem twice: once with the covariance
:meth:`~alphalab.analytics.risk_model.CovarianceMatrix.factor_model` built --
which carries its structure, so a large universe is solved by the
factor-structured method -- and once with the *same matrix* rebuilt without it,
which only the dense method can solve. The two must agree within the
tolerances, carry different problem identities (they are solved differently),
and agree to the bit below the threshold.
"""

from __future__ import annotations

import math
import random
from decimal import Decimal

import pytest

from alphalab.analytics.risk_model import Classification, CovarianceMatrix, FactorLoadings
from alphalab.portfolio_optimizer.construction import (
    FACTOR_STRUCTURED_MINIMUM_ASSETS,
    BoxUncertainty,
    ConstraintSet,
    ConstructionProblem,
    ConstructionResult,
    ConstructionStatus,
    EllipsoidalUncertainty,
    ExpectedReturns,
    ExposureRange,
    FactorBound,
    GroupBound,
    LinearCosts,
    MaximumDiversification,
    MeanVariance,
    MinimumVariance,
    RiskParity,
    RobustMeanVariance,
    SolverSettings,
    TurnoverLimit,
    WeightBounds,
    construct,
)

SETTINGS = SolverSettings(1e-9, 1e-9, 100_000)
FACTORS = ("market", "size", "value")


def _model(count: int, seed: int = 7) -> tuple[CovarianceMatrix, CovarianceMatrix, FactorLoadings]:
    """A factor-model covariance, the same matrix without its structure, and the loadings."""

    rng = random.Random(seed)
    assets = [f"A{index:04d}" for index in range(count)]
    loadings = FactorLoadings.of(
        {
            asset: {
                "market": 0.6 + 0.8 * rng.random(),
                "size": rng.gauss(0, 1),
                "value": rng.gauss(0, 1),
            }
            for asset in assets
        },
        source="synthetic model",
        lineage=dict.fromkeys(FACTORS, "synthetic"),
        as_of=None,
    )
    factor = CovarianceMatrix.from_rows(
        FACTORS,
        [[0.0004, 0.00005, 0.0], [0.00005, 0.0002, -0.00003], [0.0, -0.00003, 0.00015]],
        currency="USD",
        period="1D",
        source="synthetic",
        observations=None,
    )
    specific = {asset: 0.0001 + 0.0003 * rng.random() for asset in assets}
    structured = CovarianceMatrix.factor_model(loadings, factor, specific)
    plain = CovarianceMatrix(
        structured.assets,
        structured.values,
        structured.currency,
        structured.period,
        structured.source,
        structured.observations,
        parent_id=structured.parent_id,
        derivation=structured.derivation,
    )
    return structured, plain, loadings


def _returns(covariance: CovarianceMatrix, seed: int = 11) -> ExpectedReturns:
    rng = random.Random(seed)
    return ExpectedReturns(
        {asset: rng.gauss(0.0005, 0.001) for asset in covariance.assets}, "USD", "1D", "forecast"
    )


def _both(
    count: int, objective_for: object, constraints_for: object
) -> tuple[ConstructionResult, ConstructionResult, ConstructionProblem, ConstructionProblem]:
    structured, plain, loadings = _model(count)
    results = []
    problems = []
    for covariance in (structured, plain):
        objective = objective_for(covariance)  # type: ignore[operator]
        constraints = constraints_for(covariance, loadings)  # type: ignore[operator]
        problem = ConstructionProblem(covariance, objective, constraints, SETTINGS)
        problems.append(problem)
        results.append(construct(problem))
    return results[0], results[1], problems[0], problems[1]


def _long_only(cap: float) -> object:
    return lambda covariance, loadings: ConstraintSet(
        ExposureRange.exactly(1.0), WeightBounds.long_only(cap)
    )


def _agree(first: ConstructionResult, second: ConstructionResult, tolerance: float) -> None:
    assert first.status is ConstructionStatus.OPTIMAL, first.diagnostics.detail
    assert second.status is ConstructionStatus.OPTIMAL, second.diagnostics.detail
    assert first.weights is not None and second.weights is not None
    assert max(abs(first.weights[a] - second.weights[a]) for a in first.weights) < tolerance


def test_a_large_factor_model_is_solved_through_its_structure() -> None:
    fast, slow, fast_problem, slow_problem = _both(
        120, lambda covariance: MeanVariance(_returns(covariance), 2.0), _long_only(0.05)
    )

    _agree(fast, slow, 1e-8)
    assert "interior point" in fast.diagnostics.detail
    assert "interior point" not in slow.diagnostics.detail
    # The same claim about risk, solved two ways: two problems.
    assert fast_problem.covariance.covariance_id == slow_problem.covariance.covariance_id
    assert fast_problem.problem_id != slow_problem.problem_id
    assert fast.diagnostics.max_violation <= SETTINGS.feasibility_tolerance
    matrix = fast_problem.covariance
    assert isinstance(matrix, CovarianceMatrix)
    factors = matrix.factors
    assert factors is not None
    evidence = factors.definiteness()
    assert evidence is not None
    assert fast.diagnostics.covariance_pivot_ratio == evidence.pivot_ratio
    assert set(fast.diagnostics.binding) == set(slow.diagnostics.binding)


def test_below_the_threshold_nothing_changes_bit_for_bit() -> None:
    count = FACTOR_STRUCTURED_MINIMUM_ASSETS - 1
    fast, slow, fast_problem, slow_problem = _both(
        count, lambda covariance: MeanVariance(_returns(covariance), 2.0), _long_only(0.05)
    )

    assert fast_problem.problem_id == slow_problem.problem_id
    assert fast.result_id == slow.result_id
    assert fast.weights == slow.weights
    assert fast.diagnostics == slow.diagnostics


@pytest.mark.parametrize(
    ("objective_for", "constraints_for", "tolerance"),
    [
        pytest.param(
            lambda covariance: MinimumVariance(),
            lambda covariance, loadings: ConstraintSet(
                ExposureRange.exactly(1.0),
                WeightBounds.long_only(0.04),
                groups=(
                    GroupBound(
                        Classification(
                            "bucket",
                            {
                                asset: str(index % 4)
                                for index, asset in enumerate(covariance.assets)
                            },
                            "test",
                            None,
                        ),
                        "0",
                        0.1,
                        0.2,
                    ),
                ),
                factors=(FactorBound(loadings, "size", -0.05, 0.05),),
            ),
            1e-8,
            id="minimum-variance-groups-factor-bounds",
        ),
        pytest.param(
            lambda covariance: MeanVariance(_returns(covariance), 1.0),
            lambda covariance, loadings: ConstraintSet(
                ExposureRange.exactly(0.0),
                WeightBounds.uniform(-0.03, 0.03),
                max_gross_exposure=1.5,
            ),
            1e-8,
            id="dollar-neutral-gross-limit",
        ),
        pytest.param(
            lambda covariance: MeanVariance(_returns(covariance), 3.0),
            lambda covariance, loadings: ConstraintSet(
                ExposureRange.exactly(1.0),
                WeightBounds.long_only(0.05),
                turnover=TurnoverLimit(
                    {asset: 1.0 / len(covariance.assets) for asset in covariance.assets}, 0.3
                ),
            ),
            1e-8,
            id="turnover-limit",
        ),
        pytest.param(
            lambda covariance: MeanVariance(
                _returns(covariance),
                2.0,
                LinearCosts(
                    {asset: 1.0 / len(covariance.assets) for asset in covariance.assets},
                    dict.fromkeys(covariance.assets, 0.0004),
                ),
            ),
            _long_only(0.05),
            1e-8,
            id="linear-costs",
        ),
        pytest.param(
            lambda covariance: MeanVariance(_returns(covariance), 0.5),
            lambda covariance, loadings: ConstraintSet(
                ExposureRange.exactly(1.0), WeightBounds.long_only(0.2), max_volatility=0.016
            ),
            1e-5,
            id="volatility-cap",
        ),
        pytest.param(
            lambda covariance: MaximumDiversification(),
            _long_only(0.05),
            1e-8,
            id="maximum-diversification",
        ),
        pytest.param(
            lambda covariance: RobustMeanVariance(
                _returns(covariance), 2.0, BoxUncertainty(dict.fromkeys(covariance.assets, 0.0002))
            ),
            _long_only(0.05),
            1e-8,
            id="robust-box",
        ),
        pytest.param(
            lambda covariance: RobustMeanVariance(
                _returns(covariance),
                2.0,
                EllipsoidalUncertainty.of_sample_mean(covariance, 250, 1.0),
            ),
            _long_only(0.05),
            1e-6,
            id="robust-ellipsoidal",
        ),
    ],
)
def test_every_objective_agrees_with_the_dense_method(
    objective_for: object, constraints_for: object, tolerance: float
) -> None:
    fast, slow, fast_problem, slow_problem = _both(120, objective_for, constraints_for)

    _agree(fast, slow, tolerance)
    assert fast.diagnostics.max_violation <= SETTINGS.feasibility_tolerance
    assert fast_problem.problem_id != slow_problem.problem_id


def test_an_infeasible_problem_is_still_proved_infeasible_and_named() -> None:
    fast, slow, _, _ = _both(
        120,
        lambda covariance: MinimumVariance(),
        # 120 assets at no more than 0.5% each cannot hold the whole budget.
        _long_only(0.005),
    )

    for result in (fast, slow):
        assert result.status is ConstructionStatus.INFEASIBLE
        assert result.weights is None
        assert result.diagnostics.conflict
    assert fast.diagnostics.conflict == slow.diagnostics.conflict
    assert "factor-structured method did not certify" in fast.diagnostics.detail


def test_risk_parity_is_not_a_quadratic_program_and_keeps_its_identity() -> None:
    fast, slow, fast_problem, slow_problem = _both(
        120, lambda covariance: RiskParity(None), _long_only(1.0)
    )

    assert fast_problem.problem_id == slow_problem.problem_id
    assert fast.weights == slow.weights


def test_a_structured_problem_gives_the_same_result_every_time() -> None:
    first, _, first_problem, _ = _both(
        150, lambda covariance: MeanVariance(_returns(covariance), 2.0), _long_only(0.03)
    )
    second, _, second_problem, _ = _both(
        150, lambda covariance: MeanVariance(_returns(covariance), 2.0), _long_only(0.03)
    )

    assert first_problem.problem_id == second_problem.problem_id
    assert first.result_id == second.result_id
    assert first.weights == second.weights


def test_the_volatility_reported_is_the_covariances_own() -> None:
    fast, _, fast_problem, _ = _both(120, lambda covariance: MinimumVariance(), _long_only(0.05))

    assert fast.weights is not None and fast.diagnostics.volatility is not None
    matrix = fast_problem.covariance
    assert isinstance(matrix, CovarianceMatrix)
    rows = matrix.values
    weights = [fast.weights[asset] for asset in fast_problem.covariance.assets]
    variance = math.fsum(
        weights[i] * rows[i][j] * weights[j] for i in range(len(rows)) for j in range(len(rows))
    )
    assert fast.diagnostics.volatility == pytest.approx(math.sqrt(variance), rel=1e-12)
    assert Decimal(str(fast.diagnostics.volatility)) > 0
