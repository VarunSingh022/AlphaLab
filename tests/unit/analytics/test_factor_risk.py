"""A book's volatility divided among its factors, and exchange rates as factors (v3.13).

ADR-0043 deferred exchange-rate return factors, and stated that the risk
budget's ``CURRENCY`` dimension is the risk of holdings *denominated* in a
currency, not the risk of the exchange rate alone; the pre-v4 ledger missed
both. v3.13 divides a factor-model covariance's volatility among its factors
(:func:`factor_risk`) and builds exchange-rate loadings by denomination
(:func:`currency_loadings`).

Every figure is checked by a route that shares no code with the one under test:
hand arithmetic, :func:`euler_decomposition` over the dense covariance and over
an augmented one, and a numerical derivative.
"""

from __future__ import annotations

import math
import random

import pytest

from alphalab.analytics import (
    EXCHANGE_RATE_FACTOR_PREFIX,
    CovarianceMatrix,
    FactorLoadings,
    currency_loadings,
    euler_decomposition,
    factor_risk,
)
from alphalab.analytics.exceptions import AnalyticsValidationError


def _matrix(assets: tuple[str, ...], rows: list[list[float]]) -> CovarianceMatrix:
    return CovarianceMatrix.from_rows(
        assets, rows, currency="USD", period="1M", source="unit", observations=None
    )


def test_an_exchange_rate_carries_its_share_of_a_two_currency_book() -> None:
    """A dollar stock and a euro stock, half each, reported in dollars -- by hand.

    The euro stock loads one on ``FX:EUR``; the factor's variance is 1e-4 and
    each stock's own is 4e-4. Variance: 0.5^2 * 1e-4 + 0.25 * 4e-4 * 2 =
    2.25e-4, so volatility 0.015, of which the exchange rate carries
    0.25e-4 / 0.015.
    """

    loadings = currency_loadings({"US": "USD", "EU": "EUR"}, "USD", as_of=None)
    assert loadings.factors == ("FX:EUR",)
    assert loadings.loading("EU", "FX:EUR") == 1.0 and loadings.loading("US", "FX:EUR") == 0.0
    assert loadings.lineage["FX:EUR"] == "the return of one EUR in USD"

    model = CovarianceMatrix.factor_model(
        loadings, _matrix(("FX:EUR",), [[0.0001]]), {"US": 0.0004, "EU": 0.0004}
    )
    risk = factor_risk({"US": 0.5, "EU": 0.5}, model)

    assert risk.volatility == pytest.approx(0.015, rel=1e-15)
    assert risk.exposures["FX:EUR"] == 0.5
    assert risk.factors["FX:EUR"] == pytest.approx(0.000025 / 0.015, rel=1e-14)
    assert risk.share(EXCHANGE_RATE_FACTOR_PREFIX) == risk.factors["FX:EUR"]
    assert risk.specific["US"] == pytest.approx(0.0001 / 0.015, rel=1e-14)
    assert risk.factor_total + risk.specific_total == pytest.approx(risk.volatility, rel=1e-15)
    assert (risk.currency, risk.period) == ("USD", "1M")


def _random_model(seed: int) -> tuple[CovarianceMatrix, dict[str, float]]:
    rng = random.Random(seed)
    assets = tuple(f"A{i}" for i in range(5))
    factors = ("F1", "F2", "FX:EUR")
    raw = [[rng.gauss(0.0, 0.05) for _ in factors] for _ in range(len(factors) + 2)]
    # A positive semidefinite factor covariance: X'X / n of a random sample.
    f_rows = [
        [
            math.fsum(raw[t][a] * raw[t][b] for t in range(len(raw))) / len(raw)
            for b in range(len(factors))
        ]
        for a in range(len(factors))
    ]
    loadings = FactorLoadings.of(
        {asset: {factor: rng.uniform(-1.5, 1.5) for factor in factors} for asset in assets},
        source="random",
        lineage=dict.fromkeys(factors, "random"),
        as_of=None,
    )
    model = CovarianceMatrix.factor_model(
        loadings,
        _matrix(factors, f_rows),
        {asset: rng.uniform(0.0001, 0.001) for asset in assets},
    )
    weights = {asset: rng.uniform(-0.5, 1.0) for asset in assets}
    return model, weights


@pytest.mark.parametrize("seed", range(12))
def test_the_contributions_sum_to_the_volatility_of_the_dense_matrix(seed: int) -> None:
    model, weights = _random_model(seed)
    risk = factor_risk(weights, model)

    assert risk.volatility == pytest.approx(euler_decomposition(weights, model).volatility)
    assert abs(risk.residual) <= 1e-15 * risk.volatility
    assert set(risk.specific) == set(weights)


@pytest.mark.parametrize("seed", range(12))
def test_each_contribution_is_the_euler_term_of_an_augmented_book(seed: int) -> None:
    """Exposures as positions in the factors, under ``diag(F, D)``: the same decomposition."""

    model, weights = _random_model(seed)
    structure = model.factors
    assert structure is not None
    risk = factor_risk(weights, model)
    factors = structure.loadings.factors
    assets = structure.loadings.assets
    names = (*factors, *(f"specific:{asset}" for asset in assets))
    size = len(names)
    rows = [[0.0] * size for _ in range(size)]
    for a in range(len(factors)):
        for b in range(len(factors)):
            rows[a][b] = structure.factor_covariance.values[a][b]
    for i, variance in enumerate(structure.specific):
        rows[len(factors) + i][len(factors) + i] = variance
    augmented = CovarianceMatrix.from_rows(
        names, rows, currency="USD", period="1M", source="augmented", observations=None
    )
    book = {
        **dict(risk.exposures),
        **{f"specific:{asset}": weights[asset] for asset in assets},
    }
    reference = euler_decomposition(book, augmented)

    for factor in factors:
        assert risk.factors[factor] == pytest.approx(reference.total[factor], abs=1e-15)
    for asset in assets:
        assert risk.specific[asset] == pytest.approx(
            reference.total[f"specific:{asset}"], abs=1e-15
        )


def test_a_factor_contribution_is_its_exposure_times_the_volatilitys_slope() -> None:
    """``c_k = b_k d sigma / d b_k``, by central differences on ``sqrt(b'Fb + w'Dw)``."""

    model, weights = _random_model(99)
    structure = model.factors
    assert structure is not None
    risk = factor_risk(weights, model)
    f = structure.factor_covariance.values
    specific = math.fsum(
        weights[asset] ** 2 * variance
        for asset, variance in zip(structure.loadings.assets, structure.specific, strict=True)
    )
    exposures = [risk.exposures[factor] for factor in structure.loadings.factors]

    def sigma(b: list[float]) -> float:
        return math.sqrt(
            math.fsum(b[i] * f[i][j] * b[j] for i in range(len(b)) for j in range(len(b)))
            + specific
        )

    step = 1e-6
    for k, factor in enumerate(structure.loadings.factors):
        up, down = list(exposures), list(exposures)
        up[k] += step
        down[k] -= step
        slope = (sigma(up) - sigma(down)) / (2 * step)
        assert risk.factors[factor] == pytest.approx(exposures[k] * slope, rel=1e-6, abs=1e-12)


def test_what_cannot_be_divided_is_refused() -> None:
    dense = _matrix(("A", "B"), [[0.04, 0.01], [0.01, 0.09]])
    with pytest.raises(AnalyticsValidationError, match="not built from a factor model"):
        factor_risk({"A": 1.0}, dense)

    model, _ = _random_model(1)
    with pytest.raises(AnalyticsValidationError, match="no weights"):
        factor_risk({}, model)
    with pytest.raises(AnalyticsValidationError, match="loadings"):
        factor_risk({"ELSEWHERE": 1.0}, model)
    with pytest.raises(AnalyticsValidationError, match="weight of"):
        factor_risk({"A0": math.nan}, model)
    with pytest.raises(AnalyticsValidationError, match="volatility is zero"):
        factor_risk({"A0": 0.0}, model)


def test_currency_loadings_refuse_what_names_no_exchange_rate() -> None:
    with pytest.raises(AnalyticsValidationError, match="no exchange rate to model"):
        currency_loadings({"US": "USD"}, "USD", as_of=None)
    with pytest.raises(AnalyticsValidationError, match="ISO 4217"):
        currency_loadings({"EU": "eur"}, "USD", as_of=None)
    with pytest.raises(AnalyticsValidationError, match="ISO 4217"):
        currency_loadings({"EU": "EUR"}, "DOLLARS", as_of=None)


def test_several_currencies_each_load_on_their_own_rate() -> None:
    loadings = currency_loadings(
        {"US": "USD", "EU": "EUR", "JP": "JPY", "EU2": "EUR"}, "USD", as_of=1_700_000_000.0
    )

    assert loadings.factors == ("FX:EUR", "FX:JPY")
    assert dict(loadings.row("EU2")) == {"FX:EUR": 1.0, "FX:JPY": 0.0}
    assert dict(loadings.row("JP")) == {"FX:EUR": 0.0, "FX:JPY": 1.0}
    assert dict(loadings.row("US")) == {"FX:EUR": 0.0, "FX:JPY": 0.0}
    assert loadings.as_of == 1_700_000_000.0
