"""Black-Litterman, checked against the other form of the same posterior.

The implementation inverts only ``P (tau C) P' + Omega``. The reference here is
the *precision* form -- ``[(tau C)^-1 + P' Omega^-1 P]^-1 [(tau C)^-1 pi +
P' Omega^-1 q]`` -- computed in exact rational arithmetic. The two are equal by
the Woodbury identity and share no code, so agreement is evidence rather than
repetition.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.portfolio_optimizer.black_litterman import (
    BlackLittermanModel,
    EquilibriumPrior,
    InvestorView,
    SuppliedPrior,
    black_litterman,
    view_variance_from_prior,
)
from alphalab.portfolio_optimizer.construction import (
    ConstraintSet,
    ConstructionProblem,
    ExpectedReturns,
    ExposureRange,
    MeanVariance,
    SolverSettings,
    WeightBounds,
    construct,
)
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError

ASSETS = ("A", "B", "C")
ROWS = ((0.04, 0.01, 0.0), (0.01, 0.09, 0.02), (0.0, 0.02, 0.16))
COVARIANCE = CovarianceMatrix.from_rows(
    ASSETS, ROWS, currency="USD", period="1Y", source="unit", observations=None
)
MARKET = EquilibriumPrior(
    {"A": Decimal("600"), "B": Decimal("300"), "C": Decimal("100")}, "USD", 2.5
)
TAU = 0.05
RELATIVE = InvestorView("A beats B", {"A": 1.0, "B": -1.0}, 0.03, 0.0004)
ABSOLUTE = InvestorView("C returns 10%", {"C": 1.0}, 0.10, 0.001)


def _inverse(matrix: list[list[Fraction]]) -> list[list[Fraction]]:
    size = len(matrix)
    work = [row[:] + [Fraction(int(i == j)) for j in range(size)] for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = next(row for row in range(column, size) if work[row][column] != 0)
        work[column], work[pivot] = work[pivot], work[column]
        for row in range(size):
            if row != column and work[row][column] != 0:
                factor = work[row][column] / work[column][column]
                work[row] = [a - factor * b for a, b in zip(work[row], work[column], strict=True)]
    return [[work[i][size + j] / work[i][i] for j in range(size)] for i in range(size)]


def _precision_form(
    prior: list[float], views: tuple[InvestorView, ...]
) -> tuple[list[Fraction], list[list[Fraction]]]:
    scaled = [[Fraction(TAU) * Fraction(value) for value in row] for row in ROWS]
    precision = _inverse(scaled)
    picks = [[Fraction(view.exposures.get(asset, 0.0)) for asset in ASSETS] for view in views]
    for i in range(3):
        for j in range(3):
            precision[i][j] += sum(
                pick[i] * pick[j] / Fraction(view.variance)
                for pick, view in zip(picks, views, strict=True)
            )
    base = _inverse(scaled)
    rhs = [
        sum(base[i][j] * Fraction(prior[j]) for j in range(3))
        + sum(
            pick[i] * Fraction(view.expected_return) / Fraction(view.variance)
            for pick, view in zip(picks, views, strict=True)
        )
        for i in range(3)
    ]
    uncertainty = _inverse(precision)
    return [
        sum((uncertainty[i][j] * rhs[j] for j in range(3)), Fraction(0)) for i in range(3)
    ], uncertainty


def test_the_equilibrium_prior_is_delta_times_c_times_market_weights() -> None:
    posterior = black_litterman(BlackLittermanModel(COVARIANCE, MARKET, TAU, ()))
    weights = (0.6, 0.3, 0.1)

    for index, asset in enumerate(ASSETS):
        expected = 2.5 * sum(ROWS[index][j] * weights[j] for j in range(3))
        assert posterior.prior_returns.values[asset] == pytest.approx(expected, rel=1e-15)


def test_without_views_the_posterior_mean_is_the_prior_and_m_is_tau_c() -> None:
    posterior = black_litterman(BlackLittermanModel(COVARIANCE, MARKET, TAU, ()))

    assert dict(posterior.posterior_returns.values) == dict(posterior.prior_returns.values)
    for i in range(3):
        for j in range(3):
            assert posterior.mean_uncertainty.values[i][j] == pytest.approx(
                TAU * ROWS[i][j], rel=1e-15
            )
            assert posterior.posterior_covariance.values[i][j] == pytest.approx(
                (1 + TAU) * ROWS[i][j], rel=1e-15
            )


def test_mean_variance_on_a_view_free_posterior_holds_the_market() -> None:
    posterior = black_litterman(BlackLittermanModel(COVARIANCE, MARKET, TAU, ()))
    result = construct(
        ConstructionProblem(
            COVARIANCE,
            MeanVariance(posterior.posterior_returns, 2.5),
            ConstraintSet(ExposureRange(None, None), WeightBounds.unbounded()),
            SolverSettings(1e-12, 1e-12, 100),
        )
    )

    assert result.weights is not None
    assert dict(result.weights) == pytest.approx({"A": 0.6, "B": 0.3, "C": 0.1}, abs=1e-13)


def test_the_posterior_equals_the_exact_precision_form() -> None:
    views = (RELATIVE, ABSOLUTE)
    posterior = black_litterman(BlackLittermanModel(COVARIANCE, MARKET, TAU, views))
    prior = [posterior.prior_returns.values[asset] for asset in ASSETS]
    mean, uncertainty = _precision_form(prior, tuple(sorted(views, key=lambda v: v.name)))

    for index, asset in enumerate(ASSETS):
        assert posterior.posterior_returns.values[asset] == pytest.approx(
            float(mean[index]), abs=1e-15
        )
        for column in range(3):
            assert posterior.mean_uncertainty.values[index][column] == pytest.approx(
                float(uncertainty[index][column]), abs=1e-16
            )


def test_each_view_lands_between_its_prior_and_its_statement() -> None:
    posterior = black_litterman(BlackLittermanModel(COVARIANCE, MARKET, TAU, (RELATIVE, ABSOLUTE)))

    for diagnostic in posterior.views:
        low, high = sorted((diagnostic.prior_implied, diagnostic.stated))
        assert low < diagnostic.posterior_implied < high


def test_a_more_confident_view_pulls_the_posterior_closer() -> None:
    loose = InvestorView("C returns 10%", {"C": 1.0}, 0.10, 0.01)
    tight = InvestorView("C returns 10%", {"C": 1.0}, 0.10, 0.0001)
    loose_view = black_litterman(BlackLittermanModel(COVARIANCE, MARKET, TAU, (loose,))).views[0]
    tight_view = black_litterman(BlackLittermanModel(COVARIANCE, MARKET, TAU, (tight,))).views[0]

    assert abs(tight_view.posterior_implied - 0.10) < abs(loose_view.posterior_implied - 0.10)


def test_the_derived_matrices_are_exactly_symmetric_and_recorded() -> None:
    posterior = black_litterman(BlackLittermanModel(COVARIANCE, MARKET, TAU, (RELATIVE, ABSOLUTE)))

    for matrix in (posterior.mean_uncertainty, posterior.posterior_covariance):
        assert matrix.parent_id == COVARIANCE.covariance_id
        assert matrix.derivation is not None and posterior.model_id in matrix.derivation
        for i in range(3):
            for j in range(3):
                assert matrix.values[i][j] == matrix.values[j][i]
    assert posterior.posterior_covariance.values[0][0] == pytest.approx(
        ROWS[0][0] + posterior.mean_uncertainty.values[0][0], rel=1e-15
    )


def test_the_model_identity_ignores_view_order_and_follows_every_input() -> None:
    forward = BlackLittermanModel(COVARIANCE, MARKET, TAU, (RELATIVE, ABSOLUTE))
    backward = BlackLittermanModel(COVARIANCE, MARKET, TAU, (ABSOLUTE, RELATIVE))

    assert forward.model_id == backward.model_id
    assert black_litterman(forward) == black_litterman(backward)
    assert (
        forward.model_id
        != BlackLittermanModel(COVARIANCE, MARKET, 0.025, (RELATIVE, ABSOLUTE)).model_id
    )


def test_a_supplied_prior_is_used_as_given() -> None:
    prior = ExpectedReturns({"A": 0.05, "B": 0.07, "C": 0.04}, "USD", "1Y", "house view")
    posterior = black_litterman(BlackLittermanModel(COVARIANCE, SuppliedPrior(prior), TAU, ()))

    assert dict(posterior.prior_returns.values) == dict(prior.values)
    assert posterior.prior_returns.source == "house view"


def test_the_he_litterman_variance_is_scale_times_the_prior_variance_of_the_view() -> None:
    variance = view_variance_from_prior({"A": 1.0, "B": -1.0}, COVARIANCE, TAU, 1.0)

    assert variance == pytest.approx(TAU * (0.04 - 2 * 0.01 + 0.09), rel=1e-15)
    assert view_variance_from_prior({"A": 1.0, "B": -1.0}, COVARIANCE, TAU, 0.5) == pytest.approx(
        variance / 2
    )


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: BlackLittermanModel(COVARIANCE, MARKET, 0.0, ()), "positive"),
        (
            lambda: BlackLittermanModel(COVARIANCE, MARKET, TAU, (RELATIVE, RELATIVE)),
            "share a name",
        ),
        (
            lambda: BlackLittermanModel(
                COVARIANCE, MARKET, TAU, (InvestorView("x", {"Q": 1.0}, 0.1, 0.01),)
            ),
            "does not cover",
        ),
        (
            lambda: BlackLittermanModel(
                COVARIANCE, EquilibriumPrior({"A": Decimal("1")}, "USD", 2.5), TAU, ()
            ),
            "cover exactly",
        ),
        (
            lambda: BlackLittermanModel(
                COVARIANCE,
                EquilibriumPrior({asset: Decimal("1") for asset in ASSETS}, "EUR", 2.5),
                TAU,
                (),
            ),
            "one currency",
        ),
        (
            lambda: BlackLittermanModel(
                COVARIANCE,
                SuppliedPrior(ExpectedReturns(dict.fromkeys(ASSETS, 0.1), "USD", "1M", "s")),
                TAU,
                (),
            ),
            "currency and period",
        ),
        (lambda: InvestorView("x", {"A": 0.0}, 0.1, 0.01), "zero exposure"),
        (lambda: InvestorView("x", {}, 0.1, 0.01), "names no asset"),
        (lambda: InvestorView("x", {"A": 1.0}, 0.1, 0.0), "positive"),
        (lambda: InvestorView(" x", {"A": 1.0}, 0.1, 0.01), "unpadded"),
        (lambda: EquilibriumPrior({"A": Decimal("-1")}, "USD", 2.5), "positive Decimal"),
        (lambda: EquilibriumPrior({"A": Decimal("1")}, "USD", 0.0), "positive"),
        (lambda: EquilibriumPrior({}, "USD", 2.5), "needs market values"),
    ],
)
def test_malformed_models_are_refused(build: object, message: str) -> None:
    with pytest.raises(ConstructionInputError, match=message):
        build()  # type: ignore[operator]
