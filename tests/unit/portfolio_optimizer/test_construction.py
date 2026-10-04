"""Portfolio construction, against references that share no code with it.

Closed forms are recomputed in exact rational arithmetic; constrained
problems are handed, with constraints written out by hand here, to the
exhaustive exact enumerator in :mod:`tests.unit.portfolio_optimizer.qp_reference`;
non-quadratic objectives are checked by direct numerical search.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from decimal import Decimal
from fractions import Fraction

import pytest

from alphalab.analytics.risk_model import Classification, CovarianceMatrix, FactorLoadings
from alphalab.portfolio_optimizer import optimize_minimum_variance
from alphalab.portfolio_optimizer.construction import (
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
    MaximumDiversification,
    MeanVariance,
    MinimumVariance,
    NotionalLimits,
    RiskParity,
    RobustMeanVariance,
    SolverSettings,
    TurnoverLimit,
    WeightBounds,
    construct,
)
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError, OptimizationError
from tests.unit.portfolio_optimizer.qp_reference import Row, exact_optimum, solve_exact

ASSETS = ("A", "B", "C")
ROWS = ((0.04, 0.01, 0.0), (0.01, 0.09, 0.02), (0.0, 0.02, 0.16))
SETTINGS = SolverSettings(1e-10, 1e-10, 500)
FULL = ExposureRange.exactly(1.0)


def covariance(
    rows: Sequence[Sequence[float]] = ROWS, assets: Sequence[str] = ASSETS
) -> CovarianceMatrix:
    return CovarianceMatrix.from_rows(
        assets, rows, currency="USD", period="1M", source="unit", observations=None
    )


def returns(
    values: Mapping[str, float], currency: str = "USD", period: str = "1M"
) -> ExpectedReturns:
    return ExpectedReturns(values, currency, period, "unit forecast")


MU = returns({"A": 0.01, "B": 0.02, "C": 0.015})


def run(
    objective: object,
    constraints: ConstraintSet,
    matrix: CovarianceMatrix | None = None,
    settings: SolverSettings = SETTINGS,
) -> ConstructionResult:
    return construct(
        ConstructionProblem(
            matrix if matrix is not None else covariance(),
            objective,  # type: ignore[arg-type]
            constraints,
            settings,
        )
    )


def weights_of(result: ConstructionResult) -> list[float]:
    assert result.weights is not None, result.diagnostics.detail
    return [result.weights[asset] for asset in sorted(result.weights)]


def _inverse_times(rows: Sequence[Sequence[float]], vector: Sequence[float]) -> list[Fraction]:
    answer = solve_exact(
        [[Fraction(value) for value in row] for row in rows], [Fraction(value) for value in vector]
    )
    assert answer is not None
    return answer


def budget_row(value: float = 1.0) -> Row:
    return ([1.0, 1.0, 1.0], value, True)


def lower_rows(value: float = 0.0) -> list[Row]:
    return [([1.0 if i == j else 0.0 for j in range(3)], value, False) for i in range(3)]


def upper_rows(value: float) -> list[Row]:
    return [([-1.0 if i == j else 0.0 for j in range(3)], -value, False) for i in range(3)]


# --------------------------------------------------------------------------- #
# Minimum variance
# --------------------------------------------------------------------------- #


def test_budget_only_minimum_variance_is_the_closed_form_and_the_v1_function() -> None:
    result = run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.unbounded()))
    exact = _inverse_times(ROWS, [1.0, 1.0, 1.0])
    closed = [float(value / sum(exact)) for value in exact]
    legacy = optimize_minimum_variance(ASSETS, ROWS)

    assert result.status is ConstructionStatus.OPTIMAL
    assert weights_of(result) == pytest.approx(closed, abs=1e-14)
    assert weights_of(result) == pytest.approx([legacy[asset] for asset in ASSETS], abs=1e-14)
    assert result.diagnostics.binding == ("net exposure = 1.0",)


def test_long_only_with_caps_matches_the_exact_reference() -> None:
    result = run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(0.5)))
    reference = exact_optimum(
        ROWS, (0.0, 0.0, 0.0), [budget_row(), *lower_rows(), *upper_rows(0.5)]
    )

    assert reference is not None
    assert weights_of(result) == pytest.approx(reference, abs=1e-12)
    assert "weight[A] <= 0.5" in result.diagnostics.binding


def test_caps_that_cannot_reach_the_budget_are_infeasible_and_named() -> None:
    result = run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(0.3)))

    assert result.status is ConstructionStatus.INFEASIBLE
    assert result.weights is None
    assert set(result.diagnostics.conflict) == {
        "net exposure = 1.0",
        "weight[A] <= 0.3",
        "weight[B] <= 0.3",
        "weight[C] <= 0.3",
    }
    with pytest.raises(OptimizationError, match="INFEASIBLE"):
        result.require_weights()


def test_caps_exactly_reaching_the_budget_are_feasible_at_the_boundary() -> None:
    third = 1.0 / 3.0
    result = run(
        MinimumVariance(),
        ConstraintSet(ExposureRange.exactly(3 * third), WeightBounds.long_only(third)),
    )

    assert result.status is ConstructionStatus.OPTIMAL
    assert weights_of(result) == pytest.approx([third] * 3, abs=1e-12)


def test_a_one_asset_universe_holds_the_budget() -> None:
    single = covariance(((0.04,),), ("ONLY",))
    result = run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(None)), single)

    assert result.weights is not None
    assert dict(result.weights) == pytest.approx({"ONLY": 1.0})
    assert result.diagnostics.volatility == pytest.approx(0.2)


def test_a_zero_net_exposure_with_nothing_else_is_the_empty_portfolio() -> None:
    result = run(
        MinimumVariance(), ConstraintSet(ExposureRange.exactly(0.0), WeightBounds.unbounded())
    )

    assert weights_of(result) == pytest.approx([0.0, 0.0, 0.0], abs=1e-15)
    assert result.diagnostics.risk is None
    assert result.diagnostics.volatility == 0.0


def test_the_volatility_cap_on_minimum_variance_is_feasibility() -> None:
    lowest = run(
        MinimumVariance(), ConstraintSet(FULL, WeightBounds.unbounded())
    ).diagnostics.volatility
    assert lowest is not None

    met = run(
        MinimumVariance(),
        ConstraintSet(FULL, WeightBounds.unbounded(), max_volatility=lowest + 1e-6),
    )
    missed = run(
        MinimumVariance(),
        ConstraintSet(FULL, WeightBounds.unbounded(), max_volatility=lowest - 1e-3),
    )

    assert met.status is ConstructionStatus.OPTIMAL
    assert missed.status is ConstructionStatus.INFEASIBLE
    assert missed.diagnostics.conflict[0] == f"volatility <= {lowest - 1e-3!r}"


# --------------------------------------------------------------------------- #
# Covariance refusals and explicit regularization
# --------------------------------------------------------------------------- #


def test_a_singular_covariance_is_refused_rather_than_regularized() -> None:
    duplicated = covariance(((0.04, 0.04, 0.0), (0.04, 0.04, 0.0), (0.0, 0.0, 0.09)))

    with pytest.raises(ConstructionInputError, match="singular"):
        run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.unbounded()), duplicated)


def test_a_zero_variance_asset_is_refused_by_name() -> None:
    cash = covariance(((0.04, 0.0, 0.0), (0.0, 0.09, 0.0), (0.0, 0.0, 0.0)))

    with pytest.raises(ConstructionInputError, match="Zero-variance assets: \\['C'\\]"):
        run(RiskParity.equal(), ConstraintSet(FULL, WeightBounds.long_only(None)), cash)


def test_an_indefinite_covariance_is_refused() -> None:
    bad = covariance(((0.04, 0.05, 0.0), (0.05, 0.04, 0.0), (0.0, 0.0, 0.09)))

    with pytest.raises(ConstructionInputError, match="indefinite"):
        run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.unbounded()), bad)


def test_an_explicit_ridge_is_accepted_and_changes_the_problem_identity() -> None:
    duplicated = covariance(((0.04, 0.04, 0.0), (0.04, 0.04, 0.0), (0.0, 0.0, 0.09)))
    ridged = duplicated.with_ridge(1e-4)
    result = run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.unbounded()), ridged)

    assert result.status is ConstructionStatus.OPTIMAL
    # The two identical assets are split evenly: the ridge makes the answer unique.
    assert result.weights is not None
    assert result.weights["A"] == pytest.approx(result.weights["B"], abs=1e-12)


def test_a_near_singular_covariance_reports_its_conditioning() -> None:
    near = covariance(((0.04, 0.04 - 1e-9, 0.0), (0.04 - 1e-9, 0.04, 0.0), (0.0, 0.0, 0.09)))
    result = run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(None)), near)

    assert result.diagnostics.covariance_pivot_ratio < 1e-6
    assert result.status in (ConstructionStatus.OPTIMAL, ConstructionStatus.NUMERICAL_FAILURE)


# --------------------------------------------------------------------------- #
# Mean-variance
# --------------------------------------------------------------------------- #


def test_unconstrained_mean_variance_is_c_inverse_mu_over_lambda() -> None:
    result = run(
        MeanVariance(MU, 4.0), ConstraintSet(ExposureRange(None, None), WeightBounds.unbounded())
    )
    exact = _inverse_times(ROWS, [0.01, 0.02, 0.015])

    assert weights_of(result) == pytest.approx([float(value / 4) for value in exact], abs=1e-14)
    assert result.diagnostics.expected_return == pytest.approx(
        sum(
            float(w) * m
            for w, m in zip([value / 4 for value in exact], [0.01, 0.02, 0.015], strict=True)
        )
    )


def test_budgeted_mean_variance_is_the_lagrangian_closed_form() -> None:
    aversion = 3.0
    result = run(MeanVariance(MU, aversion), ConstraintSet(FULL, WeightBounds.unbounded()))
    inverse_ones = _inverse_times(ROWS, [1.0, 1.0, 1.0])
    inverse_mu = _inverse_times(ROWS, [0.01, 0.02, 0.015])
    gamma = (sum(inverse_mu) - Fraction(aversion)) / sum(inverse_ones)
    closed = [
        float((m - gamma * o) / Fraction(aversion))
        for m, o in zip(inverse_mu, inverse_ones, strict=True)
    ]

    assert weights_of(result) == pytest.approx(closed, abs=1e-13)


def test_long_only_mean_variance_matches_the_exact_reference() -> None:
    aversion = 2.0
    result = run(MeanVariance(MU, aversion), ConstraintSet(FULL, WeightBounds.long_only(0.6)))
    reference = exact_optimum(
        [[aversion * value for value in row] for row in ROWS],
        (-0.01, -0.02, -0.015),
        [budget_row(), *lower_rows(), *upper_rows(0.6)],
    )

    assert reference is not None
    assert weights_of(result) == pytest.approx(reference, abs=1e-12)


def test_returns_in_another_currency_or_period_are_refused() -> None:
    with pytest.raises(ConstructionInputError, match="refused as a pair"):
        run(
            MeanVariance(returns(dict(MU.values), currency="EUR"), 3.0),
            ConstraintSet(FULL, WeightBounds.unbounded()),
        )
    with pytest.raises(ConstructionInputError, match="refused as a pair"):
        run(
            MeanVariance(returns(dict(MU.values), period="1D"), 3.0),
            ConstraintSet(FULL, WeightBounds.unbounded()),
        )


def test_a_missing_expected_return_is_not_zero() -> None:
    with pytest.raises(ConstructionInputError, match="A missing expected return is not zero"):
        run(
            MeanVariance(returns({"A": 0.01, "B": 0.02}), 3.0),
            ConstraintSet(FULL, WeightBounds.unbounded()),
        )


def test_a_volatility_cap_binds_by_raising_risk_aversion() -> None:
    # At risk aversion 0.3 the long-only optimum carries about 0.195 of
    # volatility, well above the minimum-variance portfolio's 0.167, so a cap
    # between the two must bind.
    uncapped = run(MeanVariance(MU, 0.3), ConstraintSet(FULL, WeightBounds.long_only(None)))
    lowest = run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(None)))
    assert uncapped.diagnostics.volatility is not None and lowest.diagnostics.volatility is not None
    cap = (uncapped.diagnostics.volatility + lowest.diagnostics.volatility) / 2
    assert lowest.diagnostics.volatility < cap < uncapped.diagnostics.volatility

    capped = run(
        MeanVariance(MU, 0.3), ConstraintSet(FULL, WeightBounds.long_only(None), max_volatility=cap)
    )

    assert capped.status is ConstructionStatus.OPTIMAL
    assert capped.diagnostics.volatility is not None
    assert cap * (1 - 1e-9) <= capped.diagnostics.volatility <= cap + 1e-10
    assert capped.diagnostics.effective_risk_aversion is not None
    assert capped.diagnostics.effective_risk_aversion > 0.3
    assert f"volatility <= {cap!r}" in capped.diagnostics.binding


def test_the_capped_mean_variance_beats_every_feasible_grid_point() -> None:
    aversion = 1.0
    cap = 0.18
    result = run(
        MeanVariance(MU, aversion),
        ConstraintSet(FULL, WeightBounds.long_only(None), max_volatility=cap),
    )
    assert result.weights is not None

    def utility(w: Sequence[float]) -> float:
        variance = sum(w[i] * ROWS[i][j] * w[j] for i in range(3) for j in range(3))
        return (
            sum(m * x for m, x in zip((0.01, 0.02, 0.015), w, strict=True))
            - aversion / 2 * variance
        )

    best = -math.inf
    steps = 200
    for i in range(steps + 1):
        for j in range(steps + 1 - i):
            w = (i / steps, j / steps, 1 - i / steps - j / steps)
            variance = sum(w[a] * ROWS[a][b] * w[b] for a in range(3) for b in range(3))
            if math.sqrt(variance) <= cap:
                best = max(best, utility(w))
    assert utility(weights_of(result)) >= best - 1e-9


def test_a_volatility_cap_below_the_minimum_variance_is_infeasible() -> None:
    result = run(
        MeanVariance(MU, 1.0), ConstraintSet(FULL, WeightBounds.long_only(None), max_volatility=0.1)
    )

    assert result.status is ConstructionStatus.INFEASIBLE
    assert result.diagnostics.conflict[0] == "volatility <= 0.1"


# --------------------------------------------------------------------------- #
# Maximum diversification
# --------------------------------------------------------------------------- #


def _diversification(w: Sequence[float]) -> float:
    sigma = [math.sqrt(ROWS[i][i]) for i in range(3)]
    variance = sum(w[i] * ROWS[i][j] * w[j] for i in range(3) for j in range(3))
    return sum(w[i] * sigma[i] for i in range(3)) / math.sqrt(variance)


def test_unconstrained_maximum_diversification_is_c_inverse_sigma_normalized() -> None:
    result = run(MaximumDiversification(), ConstraintSet(FULL, WeightBounds.long_only(None)))
    exact = _inverse_times(ROWS, [math.sqrt(ROWS[i][i]) for i in range(3)])
    closed = [float(value / sum(exact)) for value in exact]

    assert all(value > 0 for value in closed)
    assert weights_of(result) == pytest.approx(closed, abs=1e-12)
    assert result.diagnostics.diversification_ratio == pytest.approx(
        _diversification(closed), rel=1e-12
    )


def test_no_long_only_portfolio_is_more_diversified() -> None:
    result = run(MaximumDiversification(), ConstraintSet(FULL, WeightBounds.long_only(None)))
    best = _diversification(weights_of(result))
    rng = random.Random(5)

    for _ in range(2000):
        draws = [rng.random() for _ in range(3)]
        total = sum(draws)
        assert _diversification([value / total for value in draws]) <= best + 1e-12


def test_maximum_diversification_respects_homogenized_caps_and_scales_to_the_budget() -> None:
    result = run(
        MaximumDiversification(),
        ConstraintSet(ExposureRange.exactly(0.9), WeightBounds.long_only(0.35)),
    )
    w = weights_of(result)

    assert sum(w) == pytest.approx(0.9, abs=1e-12)
    assert max(w) <= 0.35 + 1e-10
    # Among portfolios meeting the same caps, none is more diversified.
    rng = random.Random(9)
    best = _diversification(w)
    for _ in range(3000):
        draws = [rng.random() for _ in range(3)]
        scaled = [0.9 * value / sum(draws) for value in draws]
        if max(scaled) <= 0.35:
            assert _diversification(scaled) <= best + 1e-10


@pytest.mark.parametrize(
    "constraints",
    [
        ConstraintSet(FULL, WeightBounds.unbounded()),
        ConstraintSet(FULL, WeightBounds.long_only(None), max_volatility=0.2),
        ConstraintSet(
            FULL,
            WeightBounds.long_only(None),
            turnover=TurnoverLimit({"A": 0.0, "B": 0.0, "C": 1.0}, 0.5),
        ),
        ConstraintSet(ExposureRange.between(0.5, 1.0), WeightBounds.long_only(None)),
    ],
)
def test_maximum_diversification_refuses_what_it_cannot_express(constraints: ConstraintSet) -> None:
    with pytest.raises(ConstructionInputError):
        run(MaximumDiversification(), constraints)


def test_a_long_only_gross_cap_below_the_budget_is_infeasible() -> None:
    result = run(
        MaximumDiversification(),
        ConstraintSet(FULL, WeightBounds.long_only(None), max_gross_exposure=0.8),
    )

    assert result.status is ConstructionStatus.INFEASIBLE


# --------------------------------------------------------------------------- #
# Risk parity
# --------------------------------------------------------------------------- #


def _shares(w: Sequence[float], rows: Sequence[Sequence[float]]) -> list[float]:
    size = len(w)
    exposures = [sum(rows[i][j] * w[j] for j in range(size)) for i in range(size)]
    variance = sum(w[i] * exposures[i] for i in range(size))
    return [w[i] * exposures[i] / variance for i in range(size)]


def test_equal_risk_contribution_equalizes_every_share() -> None:
    result = run(RiskParity.equal(), ConstraintSet(FULL, WeightBounds.long_only(None)))
    w = weights_of(result)

    assert sum(w) == pytest.approx(1.0, abs=1e-14)
    assert _shares(w, ROWS) == pytest.approx([1 / 3] * 3, abs=1e-10)
    assert result.diagnostics.budget_deviation is not None
    assert result.diagnostics.budget_deviation <= 1e-10
    assert result.diagnostics.risk is not None
    assert dict(result.diagnostics.risk.relative) == pytest.approx(
        dict(zip(ASSETS, [1 / 3] * 3, strict=True)), abs=1e-10
    )


def test_two_asset_risk_parity_is_inverse_volatility_whatever_the_correlation() -> None:
    for correlation in (-0.6, 0.0, 0.8):
        cov = 0.2 * 0.3 * correlation
        matrix = covariance(((0.04, cov), (cov, 0.09)), ("X", "Y"))
        result = run(RiskParity.equal(), ConstraintSet(FULL, WeightBounds.long_only(None)), matrix)

        assert weights_of(result) == pytest.approx([0.6, 0.4], abs=1e-10)


def test_target_risk_budgets_are_met() -> None:
    budgets = {"A": Decimal("0.5"), "B": Decimal("0.3"), "C": Decimal("0.2")}
    result = run(RiskParity(budgets), ConstraintSet(FULL, WeightBounds.long_only(None)))

    assert _shares(weights_of(result), ROWS) == pytest.approx([0.5, 0.3, 0.2], abs=1e-10)


def test_risk_parity_scales_to_a_net_exposure_below_one() -> None:
    full = weights_of(run(RiskParity.equal(), ConstraintSet(FULL, WeightBounds.long_only(None))))
    partial = weights_of(
        run(
            RiskParity.equal(),
            ConstraintSet(ExposureRange.exactly(0.8), WeightBounds.long_only(None)),
        )
    )

    assert partial == pytest.approx([0.8 * value for value in full], abs=1e-12)


def test_a_constraint_the_unique_risk_parity_portfolio_breaks_is_infeasible() -> None:
    result = run(RiskParity.equal(), ConstraintSet(FULL, WeightBounds.long_only(0.4)))

    assert result.status is ConstructionStatus.INFEASIBLE
    assert result.diagnostics.conflict == ("risk budgets", "weight[A] <= 0.4")


def test_risk_budgets_must_be_exact_positive_shares_of_the_whole_universe() -> None:
    with pytest.raises(ConstructionInputError, match="not exactly one"):
        RiskParity({"A": Decimal("0.5"), "B": Decimal("0.3"), "C": Decimal("0.3")})
    with pytest.raises(ConstructionInputError, match="positive"):
        RiskParity({"A": Decimal("1.0"), "B": Decimal("0")})
    with pytest.raises(ConstructionInputError, match="exactly the universe"):
        run(
            RiskParity({"A": Decimal("0.5"), "B": Decimal("0.5")}),
            ConstraintSet(FULL, WeightBounds.long_only(None)),
        )
    with pytest.raises(ConstructionInputError, match="exact, positive net exposure"):
        run(
            RiskParity.equal(),
            ConstraintSet(ExposureRange.exactly(0.0), WeightBounds.long_only(None)),
        )


def test_risk_parity_that_has_not_converged_is_reported_as_such() -> None:
    result = run(
        RiskParity({"A": Decimal("0.9"), "B": Decimal("0.05"), "C": Decimal("0.05")}),
        ConstraintSet(FULL, WeightBounds.long_only(None)),
        settings=SolverSettings(1e-10, 1e-15, 1),
    )

    assert result.status is ConstructionStatus.ITERATION_LIMIT
    assert result.weights is None
    assert result.diagnostics.budget_deviation is not None


# --------------------------------------------------------------------------- #
# Robust mean-variance
# --------------------------------------------------------------------------- #


def test_ellipsoidal_robustness_maximizes_the_worst_case_objective() -> None:
    matrix = covariance(((0.04, 0.012), (0.012, 0.09)), ("X", "Y"))
    mu = returns({"X": 0.02, "Y": 0.035})
    uncertainty = EllipsoidalUncertainty.of_sample_mean(matrix, 24, 1.5)
    omega = uncertainty.omega.values
    aversion = 3.0
    result = run(
        RobustMeanVariance(mu, aversion, uncertainty),
        ConstraintSet(FULL, WeightBounds.unbounded()),
        matrix,
    )

    def worst(t: float) -> float:
        w = (t, 1.0 - t)

        def quad(rows: Sequence[Sequence[float]]) -> float:
            return sum(w[i] * rows[i][j] * w[j] for i in range(2) for j in range(2))

        return (
            0.02 * w[0]
            + 0.035 * w[1]
            - 1.5 * math.sqrt(quad(omega))
            - aversion / 2 * quad(matrix.values)
        )

    low, high = -5.0, 5.0
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    for _ in range(200):
        a = high - ratio * (high - low)
        b = low + ratio * (high - low)
        if worst(a) < worst(b):
            low = a
        else:
            high = b
    best = (low + high) / 2.0

    assert weights_of(result)[0] == pytest.approx(best, abs=1e-6)
    assert result.diagnostics.worst_case_return is not None
    assert result.diagnostics.expected_return is not None
    assert result.diagnostics.worst_case_return < result.diagnostics.expected_return


def test_a_zero_radius_is_the_nominal_problem() -> None:
    nominal = run(MeanVariance(MU, 3.0), ConstraintSet(FULL, WeightBounds.long_only(None)))
    robust = run(
        RobustMeanVariance(MU, 3.0, EllipsoidalUncertainty.of_sample_mean(covariance(), 60, 0.0)),
        ConstraintSet(FULL, WeightBounds.long_only(None)),
    )

    assert weights_of(robust) == pytest.approx(weights_of(nominal), abs=1e-15)


def test_a_box_set_on_a_long_only_book_is_mean_variance_on_shifted_returns() -> None:
    widths = {"A": 0.004, "B": 0.012, "C": 0.001}
    shifted = returns({asset: MU.values[asset] - widths[asset] for asset in ASSETS})
    robust = run(
        RobustMeanVariance(MU, 3.0, BoxUncertainty(widths)),
        ConstraintSet(FULL, WeightBounds.long_only(None)),
    )
    reference = run(MeanVariance(shifted, 3.0), ConstraintSet(FULL, WeightBounds.long_only(None)))

    assert weights_of(robust) == pytest.approx(weights_of(reference), abs=1e-15)


def test_a_box_set_where_shorts_are_allowed_is_solved_as_an_l1_penalty() -> None:
    """Refused until v3.13; ``test_robust_box_with_shorts.py`` checks it by brute force."""

    nominal = run(MeanVariance(MU, 3.0), ConstraintSet(FULL, WeightBounds.unbounded()))
    robust = run(
        RobustMeanVariance(MU, 3.0, BoxUncertainty({"A": 0.0, "B": 0.0, "C": 0.0})),
        ConstraintSet(FULL, WeightBounds.unbounded()),
    )

    assert weights_of(robust) == pytest.approx(weights_of(nominal), abs=1e-12)


def test_an_uncertainty_set_must_match_the_universe_and_units() -> None:
    other = covariance(((0.04, 0.0), (0.0, 0.09)), ("A", "B"))
    with pytest.raises(ConstructionInputError, match="exactly the construction universe"):
        run(
            RobustMeanVariance(MU, 3.0, EllipsoidalUncertainty(other, 1.0)),
            ConstraintSet(FULL, WeightBounds.long_only(None)),
        )
    with pytest.raises(ConstructionInputError, match="half width for exactly the universe"):
        run(
            RobustMeanVariance(MU, 3.0, BoxUncertainty({"A": 0.1})),
            ConstraintSet(FULL, WeightBounds.long_only(None)),
        )


def test_robustness_with_a_volatility_cap_stays_under_the_cap() -> None:
    cap = 0.175
    result = run(
        RobustMeanVariance(MU, 1.0, EllipsoidalUncertainty.of_sample_mean(covariance(), 60, 1.0)),
        ConstraintSet(FULL, WeightBounds.long_only(None), max_volatility=cap),
    )

    assert result.status is ConstructionStatus.OPTIMAL
    assert result.diagnostics.volatility is not None
    assert result.diagnostics.volatility <= cap + 1e-10


# --------------------------------------------------------------------------- #
# Factor, group, turnover, gross, notional and concentration constraints
# --------------------------------------------------------------------------- #

LOADINGS = FactorLoadings.of(
    {
        "A": {"mkt": 0.8, "size": 0.3},
        "B": {"mkt": 1.2, "size": -0.4},
        "C": {"mkt": 1.0, "size": 0.1},
    },
    source="unit model",
    lineage={"mkt": "beta", "size": "size score"},
    as_of=None,
)


def test_a_factor_neutral_portfolio_has_zero_exposure_and_matches_the_reference() -> None:
    result = run(
        MinimumVariance(),
        ConstraintSet(
            FULL, WeightBounds.unbounded(), factors=(FactorBound.neutral(LOADINGS, "size"),)
        ),
    )
    reference = exact_optimum(ROWS, (0.0, 0.0, 0.0), [budget_row(), ([0.3, -0.4, 0.1], 0.0, True)])

    assert reference is not None
    assert weights_of(result) == pytest.approx(reference, abs=1e-12)
    assert result.diagnostics.factor_exposures["size"] == pytest.approx(0.0, abs=1e-12)


def test_a_factor_target_band_holds_the_exposure_inside_it() -> None:
    bound = FactorBound.target(LOADINGS, "mkt", 1.1, 0.02)
    result = run(
        MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(None), factors=(bound,))
    )

    assert 1.08 - 1e-10 <= result.diagnostics.factor_exposures["mkt"] <= 1.12 + 1e-10


def test_factor_bounds_refuse_uncovered_assets_unknown_factors_and_two_models() -> None:
    partial = FactorLoadings.of({"A": {"mkt": 1.0}}, source="m", lineage={"mkt": "x"}, as_of=None)
    with pytest.raises(ConstructionInputError, match="not an asset with a zero loading"):
        run(
            MinimumVariance(),
            ConstraintSet(
                FULL, WeightBounds.unbounded(), factors=(FactorBound.neutral(partial, "mkt"),)
            ),
        )
    with pytest.raises(ConstructionInputError, match="not in loadings"):
        FactorBound.neutral(LOADINGS, "value")
    other = FactorLoadings.of(
        {asset: {"mkt": 1.0} for asset in ASSETS}, source="other", lineage={"mkt": "y"}, as_of=None
    )
    with pytest.raises(ConstructionInputError, match="two different factor models"):
        ConstraintSet(
            FULL,
            WeightBounds.unbounded(),
            factors=(FactorBound.neutral(LOADINGS, "mkt"), FactorBound(other, "mkt", 0.0, 2.0)),
        )


def test_a_factor_every_asset_is_neutral_to_cannot_be_given_exposure() -> None:
    flat = FactorLoadings.of(
        {asset: {"f": 0.0} for asset in ASSETS}, source="m", lineage={"f": "x"}, as_of=None
    )
    result = run(
        MinimumVariance(),
        ConstraintSet(FULL, WeightBounds.unbounded(), factors=(FactorBound(flat, "f", 0.5, None),)),
    )

    assert result.status is ConstructionStatus.INFEASIBLE
    assert "factor[f]" in result.diagnostics.conflict[0]


SECTORS = Classification("sector", {"A": "Tech", "B": "Tech", "C": "Energy"}, "unit registry", None)


def test_a_sector_cap_binds_and_matches_the_reference() -> None:
    result = run(
        MinimumVariance(),
        ConstraintSet(
            FULL, WeightBounds.long_only(None), groups=(GroupBound(SECTORS, "Tech", None, 0.5),)
        ),
    )
    reference = exact_optimum(
        ROWS, (0.0, 0.0, 0.0), [budget_row(), *lower_rows(), ([-1.0, -1.0, 0.0], -0.5, False)]
    )

    assert reference is not None
    assert weights_of(result) == pytest.approx(reference, abs=1e-12)
    assert result.diagnostics.group_exposures["sector[Tech]"] == pytest.approx(0.5, abs=1e-12)
    assert "sector[Tech] <= 0.5" in result.diagnostics.binding


def test_an_unclassified_asset_is_refused_not_left_out_of_every_group() -> None:
    partial = Classification("sector", {"A": "Tech", "B": "Tech"}, "unit registry", None)
    with pytest.raises(ConstructionInputError, match=r"needs a sector for \['C'\]"):
        run(
            MinimumVariance(),
            ConstraintSet(
                FULL, WeightBounds.long_only(None), groups=(GroupBound(partial, "Tech", None, 0.5),)
            ),
        )


def test_a_bound_on_an_empty_group_holds_at_zero_or_is_infeasible() -> None:
    empty = GroupBound(SECTORS, "Utilities", None, 0.1)
    impossible = GroupBound(SECTORS, "Utilities", 0.1, None)

    assert run(
        MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(None), groups=(empty,))
    ).succeeded
    blocked = run(
        MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(None), groups=(impossible,))
    )
    assert blocked.status is ConstructionStatus.INFEASIBLE
    assert "Utilities" in blocked.diagnostics.conflict[0]


def test_a_currency_group_is_just_another_classification() -> None:
    currencies = Classification("currency", {"A": "USD", "B": "EUR", "C": "EUR"}, "registry", None)
    result = run(
        MinimumVariance(),
        ConstraintSet(
            FULL, WeightBounds.long_only(None), groups=(GroupBound(currencies, "EUR", 0.5, None),)
        ),
    )

    assert result.diagnostics.group_exposures["currency[EUR]"] >= 0.5 - 1e-10


def test_a_turnover_limit_matches_the_reference_with_its_facets_written_out() -> None:
    current = {"A": 0.0, "B": 0.0, "C": 1.0}
    result = run(
        MinimumVariance(),
        ConstraintSet(FULL, WeightBounds.unbounded(), turnover=TurnoverLimit(current, 0.2)),
    )
    facets: list[Row] = []
    for signs in ((a, b, c) for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)):
        facets.append(
            (
                [-float(s) for s in signs],
                -0.2 - sum(s * w for s, w in zip(signs, (0.0, 0.0, 1.0), strict=True)),
                False,
            )
        )
    reference = exact_optimum(ROWS, (0.0, 0.0, 0.0), [budget_row(), *facets])

    assert reference is not None
    assert weights_of(result) == pytest.approx(reference, abs=1e-12)
    assert result.diagnostics.turnover == pytest.approx(0.2, abs=1e-10)


def test_turnover_needs_exactly_the_universe_zeros_included() -> None:
    with pytest.raises(ConstructionInputError, match="not assumed unheld"):
        run(
            MinimumVariance(),
            ConstraintSet(FULL, WeightBounds.unbounded(), turnover=TurnoverLimit({"A": 1.0}, 0.2)),
        )


def test_a_gross_exposure_cap_limits_leverage_in_a_long_short_book() -> None:
    # At risk aversion 0.05 the unconstrained optimum is levered to a gross
    # exposure of about 3.4, so a cap of 1.5 must bind.
    free = run(MeanVariance(MU, 0.05), ConstraintSet(FULL, WeightBounds.unbounded()))
    assert free.diagnostics.gross_exposure is not None and free.diagnostics.gross_exposure > 1.5
    capped = run(
        MeanVariance(MU, 0.05),
        ConstraintSet(FULL, WeightBounds.unbounded(), max_gross_exposure=1.5),
    )

    assert capped.diagnostics.gross_exposure == pytest.approx(1.5, abs=1e-10)
    assert capped.diagnostics.net_exposure == pytest.approx(1.0, abs=1e-12)


def test_notional_limits_become_weight_bounds_in_the_covariance_currency() -> None:
    limits = NotionalLimits(Decimal("1000000"), {"A": Decimal("250000")})
    result = run(
        MinimumVariance(), ConstraintSet(FULL, WeightBounds.unbounded(), notional_limits=limits)
    )

    assert result.weights is not None
    assert result.weights["A"] == pytest.approx(0.25, abs=1e-12)
    assert "notional[A] <= 0.25" in result.diagnostics.binding


def test_notional_limits_refuse_unknown_assets_and_bad_amounts() -> None:
    with pytest.raises(ConstructionInputError, match="outside the universe"):
        run(
            MinimumVariance(),
            ConstraintSet(
                FULL,
                WeightBounds.unbounded(),
                notional_limits=NotionalLimits(Decimal("1"), {"Q": Decimal("1")}),
            ),
        )
    with pytest.raises(ConstructionInputError, match="positive, finite"):
        NotionalLimits(Decimal("0"), {"A": Decimal("1")})
    with pytest.raises(ConstructionInputError, match="non-negative"):
        NotionalLimits(Decimal("1"), {"A": Decimal("-1")})


def test_concentration_caps_every_absolute_weight() -> None:
    result = run(
        MeanVariance(MU, 1.0), ConstraintSet(FULL, WeightBounds.unbounded(), max_abs_weight=0.45)
    )

    assert max(abs(value) for value in weights_of(result)) <= 0.45 + 1e-10


def test_bounds_on_an_unknown_asset_are_refused() -> None:
    with pytest.raises(ConstructionInputError, match="outside the universe"):
        run(
            MinimumVariance(),
            ConstraintSet(FULL, WeightBounds.unbounded().with_asset("Q", 0.0, 0.1)),
        )


# --------------------------------------------------------------------------- #
# Diagnostics, identity and determinism
# --------------------------------------------------------------------------- #


def test_diagnostics_decompose_the_risk_of_the_weights_returned() -> None:
    result = run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(0.5)))
    risk = result.diagnostics.risk

    assert risk is not None
    assert math.fsum(risk.total.values()) == pytest.approx(risk.volatility, rel=1e-13)
    assert dict(risk.weights) == pytest.approx(dict(result.weights or {}))
    assert result.diagnostics.max_violation <= SETTINGS.feasibility_tolerance
    assert result.diagnostics.iterations >= 1
    assert result.diagnostics.method == "MinimumVariance"


def test_the_problem_identity_ignores_constraint_order_and_follows_everything_else() -> None:
    tech = GroupBound(SECTORS, "Tech", None, 0.6)
    energy = GroupBound(SECTORS, "Energy", 0.1, None)
    first = ConstructionProblem(
        covariance(),
        MinimumVariance(),
        ConstraintSet(FULL, WeightBounds.long_only(None), groups=(tech, energy)),
        SETTINGS,
    )
    second = ConstructionProblem(
        covariance(),
        MinimumVariance(),
        ConstraintSet(FULL, WeightBounds.long_only(None), groups=(energy, tech)),
        SETTINGS,
    )
    other = ConstructionProblem(
        covariance(),
        MinimumVariance(),
        ConstraintSet(FULL, WeightBounds.long_only(None), groups=(tech,)),
        SETTINGS,
    )
    looser = ConstructionProblem(
        covariance(),
        MinimumVariance(),
        ConstraintSet(FULL, WeightBounds.long_only(None), groups=(tech, energy)),
        SolverSettings(1e-9, 1e-10, 500),
    )

    assert first.problem_id == second.problem_id
    assert first.problem_id != other.problem_id
    assert first.problem_id != looser.problem_id
    assert construct(first).result_id == construct(second).result_id


def test_the_same_problem_gives_the_same_result_twice() -> None:
    problem = ConstructionProblem(
        covariance(),
        RobustMeanVariance(MU, 2.0, EllipsoidalUncertainty.of_sample_mean(covariance(), 36, 1.0)),
        ConstraintSet(
            FULL,
            WeightBounds.long_only(0.6),
            factors=(FactorBound.target(LOADINGS, "mkt", 1.0, 0.1),),
        ),
        SETTINGS,
    )

    assert construct(problem) == construct(problem)


def test_an_infeasible_result_has_an_identity_that_names_its_conflict() -> None:
    result = run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(0.3)))

    assert not result.succeeded
    assert (
        result.result_id
        != run(MinimumVariance(), ConstraintSet(FULL, WeightBounds.long_only(0.5))).result_id
    )


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("feasibility", "convergence", "iterations"),
    [(0.0, 1e-9, 10), (1e-9, 1.0, 10), (1e-9, 1e-9, 0), (1e-9, 1e-9, True)],
)
def test_solver_settings_are_validated(
    feasibility: float, convergence: float, iterations: int
) -> None:
    with pytest.raises(ConstructionInputError):
        SolverSettings(feasibility, convergence, iterations)


def test_ranges_and_bounds_refuse_an_empty_interval() -> None:
    with pytest.raises(ConstructionInputError, match="empty"):
        ExposureRange(1.0, 0.0)
    with pytest.raises(ConstructionInputError, match="empty"):
        WeightBounds.uniform(0.5, 0.1)
    with pytest.raises(ConstructionInputError, match="empty"):
        WeightBounds.unbounded().with_asset("A", 0.5, 0.1)
    with pytest.raises(ConstructionInputError, match="neither end"):
        GroupBound(SECTORS, "Tech", None, None)
    with pytest.raises(ConstructionInputError, match="negative"):
        TurnoverLimit({"A": 0.0}, -0.1)
    with pytest.raises(ConstructionInputError, match="magnitude"):
        ConstraintSet(FULL, WeightBounds.unbounded(), max_gross_exposure=-1.0)
    with pytest.raises(ConstructionInputError, match="twice"):
        ConstraintSet(
            FULL, WeightBounds.unbounded(), groups=(GroupBound(SECTORS, "Tech", None, 0.5),) * 2
        )


def test_objectives_validate_their_parameters() -> None:
    with pytest.raises(ConstructionInputError, match="positive"):
        MeanVariance(MU, 0.0)
    with pytest.raises(ConstructionInputError, match="positive"):
        RobustMeanVariance(MU, -1.0, BoxUncertainty({"A": 0.0}))
    with pytest.raises(ConstructionInputError, match="negative"):
        EllipsoidalUncertainty(covariance(), -1.0)
    with pytest.raises(ConstructionInputError, match="at least 2"):
        EllipsoidalUncertainty.of_sample_mean(covariance(), 1, 1.0)
    with pytest.raises(ConstructionInputError, match="negative"):
        BoxUncertainty({"A": -0.1})
    with pytest.raises(ConstructionInputError, match="describe nothing"):
        ExpectedReturns({}, "USD", "1M", "s")
    with pytest.raises(ConstructionInputError, match="finite"):
        ExpectedReturns({"A": float("inf")}, "USD", "1M", "s")
