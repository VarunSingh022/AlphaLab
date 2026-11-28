"""Principal-component factors: a covariance's components as a factor model (v4.3, WS-25).

* **The components are the eigensystem's**: ``C v = lambda v`` for every kept
  component, the loadings orthonormal, and a closed-form two-asset case exact.
* **Nothing is lost on the diagonal**: the model's implied variance of each
  asset is the covariance's, the specific variance being what the components
  left out carry.
* **A low-rank covariance with isotropic noise** leaves the noise's variance
  in every component left out.
* **What it cannot take is refused**: a singular covariance, which the caller
  regularizes with a recorded derivation, and a count of factors out of range.
"""

from __future__ import annotations

import math
import random

import pytest

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.analytics.statistical_factors import principal_component_factors

TOLERANCE = 1e-15
ITERATIONS = 60


def _covariance(rows: list[list[float]], assets: list[str] | None = None) -> CovarianceMatrix:
    names = assets or [f"A{i:02d}" for i in range(len(rows))]
    return CovarianceMatrix.from_rows(
        names, rows, currency="USD", period="1D", source="test", observations=None
    )


def _components(covariance: CovarianceMatrix, factors: int):  # type: ignore[no-untyped-def]
    return principal_component_factors(
        covariance, factors=factors, tolerance=TOLERANCE, max_iterations=ITERATIONS
    )


def test_two_assets_give_their_closed_form() -> None:
    result = _components(_covariance([[2.0, 1.0], [1.0, 2.0]]), 1)
    structure = result.structure

    assert result.eigenvalues == pytest.approx((3.0, 1.0), rel=1e-15)
    assert result.explained == pytest.approx((0.75,), rel=1e-15)
    half = math.sqrt(0.5)
    assert [structure.loadings.loading(a, "pc_1") for a in ("A00", "A01")] == pytest.approx(
        [half, half], rel=1e-15
    )
    assert structure.factor_covariance.variance("pc_1") == pytest.approx(3.0, rel=1e-15)
    assert structure.specific == pytest.approx((0.5, 0.5), rel=1e-14)
    implied = structure.implied
    assert implied[0][0] == pytest.approx(2.0, rel=1e-14)
    assert implied[0][1] == pytest.approx(1.5, rel=1e-14)


def _sample(seed: int, assets: int, observations: int) -> CovarianceMatrix:
    draws = random.Random(seed)
    loadings = [[draws.gauss(0.0, 1.0) for _ in range(2)] for _ in range(assets)]
    returns = []
    for _ in range(observations):
        factors = [draws.gauss(0.0, 0.02), draws.gauss(0.0, 0.01)]
        returns.append(
            [
                math.fsum(b * f for b, f in zip(row, factors, strict=True))
                + draws.gauss(0.0, 0.005)
                for row in loadings
            ]
        )
    names = [f"A{i:02d}" for i in range(assets)]
    return CovarianceMatrix.sample(
        {name: [r[i] for r in returns] for i, name in enumerate(names)},
        currency="USD",
        period="1D",
        source="simulated",
    )


def test_each_kept_component_solves_the_eigen_equation_and_the_loadings_are_orthonormal() -> None:
    covariance = _sample(5, 8, 300)
    result = _components(covariance, 3)
    loadings = result.structure.loadings
    rows = covariance.values

    for j, name in enumerate(loadings.factors):
        vector = [loadings.loading(asset, name) for asset in covariance.assets]
        product = [math.fsum(a * v for a, v in zip(row, vector, strict=True)) for row in rows]
        assert product == pytest.approx(
            [result.eigenvalues[j] * v for v in vector], rel=1e-10, abs=1e-16
        )
        for m, other in enumerate(loadings.factors):
            dot = math.fsum(
                loadings.loading(a, name) * loadings.loading(a, other) for a in covariance.assets
            )
            assert dot == pytest.approx(1.0 if j == m else 0.0, abs=1e-13)
    assert list(result.eigenvalues) == sorted(result.eigenvalues, reverse=True)
    assert result.explained == pytest.approx(
        [value / math.fsum(result.eigenvalues) for value in result.eigenvalues[:3]], rel=1e-15
    )
    assert result.convergence.reached <= TOLERANCE


def test_the_model_keeps_every_assets_variance() -> None:
    covariance = _sample(6, 7, 250)
    implied = _components(covariance, 2).structure.implied

    for i in range(7):
        assert implied[i][i] == pytest.approx(covariance.values[i][i], rel=1e-12)


def test_isotropic_noise_is_what_the_components_left_out_carry() -> None:
    draws = random.Random(9)
    loadings = [[draws.gauss(0.0, 1.0) for _ in range(2)] for _ in range(6)]
    noise = 0.25
    rows = [
        [
            math.fsum(a * b for a, b in zip(loadings[i], loadings[k], strict=True))
            + (noise if i == k else 0.0)
            for k in range(6)
        ]
        for i in range(6)
    ]
    result = _components(_covariance(rows), 2)

    assert result.eigenvalues[2:] == pytest.approx([noise] * 4, rel=1e-12)
    structure = result.structure
    for i, asset in enumerate(structure.assets):
        kept = math.fsum(
            structure.loadings.loading(asset, f) ** 2 for f in structure.loadings.factors
        )
        assert structure.specific[i] == pytest.approx(noise * (1.0 - kept), rel=1e-10)


def test_ten_factors_sort_in_their_order() -> None:
    result = _components(_sample(7, 12, 400), 10)

    assert result.structure.loadings.factors == tuple(f"pc_{j:02d}" for j in range(1, 11))
    variances = [
        result.structure.factor_covariance.variance(f) for f in result.structure.loadings.factors
    ]
    assert variances == list(result.eigenvalues[:10])


def test_a_singular_covariance_is_refused_for_the_caller_to_regularize() -> None:
    singular = _covariance([[1.0, 1.0, 0.5], [1.0, 1.0, 0.5], [0.5, 0.5, 1.0]])
    with pytest.raises(AnalyticsValidationError, match="singular"):
        _components(singular, 1)


@pytest.mark.parametrize("factors", [0, 3, True, 1.0])
def test_a_count_of_factors_out_of_range_is_refused(factors: object) -> None:
    with pytest.raises(AnalyticsValidationError, match="factors is an integer from 1 to 2"):
        _components(_covariance([[2.0, 0.5, 0.0], [0.5, 1.0, 0.0], [0.0, 0.0, 1.0]]), factors)  # type: ignore[arg-type]


def test_what_is_not_a_covariance_is_refused() -> None:
    with pytest.raises(AnalyticsValidationError, match="must be a CovarianceMatrix"):
        _components([[1.0]], 1)  # type: ignore[arg-type]
