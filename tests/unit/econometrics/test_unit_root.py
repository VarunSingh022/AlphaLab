"""Unit-root and stationarity tests (v4.3, ADR-0074, WS-07).

* The ADF statistic is the Dickey-Fuller regression's t-statistic, held to
  exact arithmetic; Phillips-Perron with no long-run correction is it exactly.
* On simulated data the tests reject what they should: a random walk's unit
  root is not rejected and its stationarity is; a stationary AR(1)'s is the
  reverse. Under the null, ADF rejects at its nominal rate.
* Lag selection finds a known order; the Schwert bandwidth is exact.
* Every result carries its regression's estimate, whose identity covers the
  series and every choice; every refusal is held.
"""

from __future__ import annotations

import random
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from alphalab.econometrics._regression import ordinary_least_squares
from alphalab.econometrics.critical_values import Deterministic, PBound
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import series_identity
from alphalab.econometrics.unit_root import (
    FixedBandwidth,
    FixedLags,
    LagCriterion,
    Null,
    SchwertBandwidth,
    SelectedLags,
    augmented_dickey_fuller,
    kpss,
    phillips_perron,
)

CONDITION = 1e8


def _walk(seed: int, count: int) -> list[float]:
    draws = random.Random(seed)
    values = [0.0]
    for _ in range(count - 1):
        values.append(values[-1] + draws.gauss(0.0, 1.0))
    return values


def _ar(seed: int, count: int, phi: float) -> list[float]:
    draws = random.Random(seed)
    values = [0.0]
    for _ in range(count - 1):
        values.append(phi * values[-1] + draws.gauss(0.0, 1.0))
    return values


NO_LAGS = FixedLags(0)


def _adf(values: list[float], lags: FixedLags | SelectedLags = NO_LAGS) -> float:
    return augmented_dickey_fuller(
        values, deterministic=Deterministic.CONSTANT, lags=lags, maximum_condition=CONDITION
    ).statistic


def test_the_adf_statistic_is_the_dickey_fuller_t_statistic_in_exact_arithmetic() -> None:
    values = [
        float(v)
        for v in (
            3,
            5,
            4,
            6,
            8,
            7,
            9,
            12,
            10,
            11,
            13,
            12,
            15,
            14,
            16,
            18,
            17,
            19,
            21,
            20,
            22,
            21,
            24,
        )
    ]
    test = augmented_dickey_fuller(
        values, deterministic=Deterministic.CONSTANT, lags=FixedLags(0), maximum_condition=CONDITION
    )

    y = [Fraction(v) for v in values]
    target = [y[t] - y[t - 1] for t in range(1, len(y))]
    lagged = y[:-1]
    n = len(target)
    sx, sxx = sum(lagged), sum(v * v for v in lagged)
    sy, sxy = sum(target), sum(a * b for a, b in zip(lagged, target, strict=True))
    det = n * sxx - sx * sx
    rho = (n * sxy - sx * sy) / det
    constant = (sy - rho * sx) / n
    s2 = sum((v - constant - rho * x) ** 2 for v, x in zip(target, lagged, strict=True)) / (n - 2)
    t_squared = rho * rho / (s2 * n / det)

    assert test.observations == n
    assert test.statistic < 0
    assert test.statistic**2 == pytest.approx(float(t_squared), rel=1e-12)
    assert test.regression.parameters["level_lag"] == pytest.approx(float(rho), rel=1e-12)


def test_phillips_perron_with_no_correction_is_the_dickey_fuller_statistic() -> None:
    values = _walk(3, 120)
    no_correction = phillips_perron(
        values,
        deterministic=Deterministic.CONSTANT,
        bandwidth=FixedBandwidth(0),
        maximum_condition=CONDITION,
    )

    assert no_correction.statistic == _adf(values)


def test_a_random_walk_keeps_its_unit_root_and_loses_its_stationarity() -> None:
    walk = _walk(7, 500)

    adf = augmented_dickey_fuller(
        walk,
        deterministic=Deterministic.CONSTANT,
        lags=SelectedLags(LagCriterion.AIC, 8, None),
        maximum_condition=CONDITION,
    )
    pp = phillips_perron(
        walk,
        deterministic=Deterministic.CONSTANT,
        bandwidth=SchwertBandwidth(4),
        maximum_condition=CONDITION,
    )
    stationarity = kpss(
        walk,
        deterministic=Deterministic.CONSTANT,
        bandwidth=SchwertBandwidth(12),
        maximum_condition=CONDITION,
    )

    assert adf.null is Null.UNIT_ROOT and adf.statistic > adf.critical_values.values[0.05]
    assert adf.p_value.value > 0.10
    assert pp.statistic > pp.critical_values.values[0.05]
    assert stationarity.null is Null.STATIONARITY
    assert stationarity.statistic > stationarity.critical_values.values[0.01]
    assert stationarity.p_value.bound is PBound.AT_MOST and stationarity.p_value.value == 0.01


def test_a_stationary_series_loses_its_unit_root_and_keeps_its_stationarity() -> None:
    stationary = _ar(7, 500, 0.5)

    adf = augmented_dickey_fuller(
        stationary,
        deterministic=Deterministic.CONSTANT,
        lags=SelectedLags(LagCriterion.BIC, 8, None),
        maximum_condition=CONDITION,
    )
    pp = phillips_perron(
        stationary,
        deterministic=Deterministic.CONSTANT,
        bandwidth=SchwertBandwidth(4),
        maximum_condition=CONDITION,
    )
    stationarity = kpss(
        stationary,
        deterministic=Deterministic.CONSTANT,
        bandwidth=SchwertBandwidth(12),
        maximum_condition=CONDITION,
    )

    assert adf.statistic < adf.critical_values.values[0.01] and adf.p_value.value < 0.001
    assert pp.statistic < pp.critical_values.values[0.01]
    assert stationarity.statistic < stationarity.critical_values.values[0.10]


def test_under_the_null_adf_rejects_at_its_nominal_rate_and_otherwise_almost_always() -> None:
    """200 random walks of 100: rejections at 5% fall within three binomial deviations."""

    rejections = 0
    detected = 0
    for seed in range(200):
        null = augmented_dickey_fuller(
            _walk(1000 + seed, 100),
            deterministic=Deterministic.CONSTANT,
            lags=FixedLags(0),
            maximum_condition=CONDITION,
        )
        rejections += null.statistic < null.critical_values.values[0.05]
        alternative = augmented_dickey_fuller(
            _ar(5000 + seed, 100, 0.5),
            deterministic=Deterministic.CONSTANT,
            lags=FixedLags(0),
            maximum_condition=CONDITION,
        )
        detected += alternative.statistic < alternative.critical_values.values[0.05]

    assert 0.05 - 3 * 0.0154 <= rejections / 200 <= 0.05 + 3 * 0.0154
    assert detected / 200 > 0.9


def _ar_differences(seed: int, count: int) -> list[float]:
    """Levels whose differences follow ``Δy_t = 0.6 Δy_{t-1} + ε_t``: one lagged difference."""

    draws = random.Random(seed)
    differences = [0.0]
    for _ in range(count - 1):
        differences.append(0.6 * differences[-1] + draws.gauss(0.0, 1.0))
    levels = [0.0]
    for d in differences[1:]:
        levels.append(levels[-1] + d)
    return levels


def test_bic_chooses_the_least_criterion_on_the_maximums_sample() -> None:
    """The selection by construction: BIC recomputed by hand on the common sample."""

    levels = _ar_differences(11, 300)
    maximum = 4
    test = augmented_dickey_fuller(
        levels,
        deterministic=Deterministic.CONSTANT,
        lags=SelectedLags(LagCriterion.BIC, maximum, None),
        maximum_condition=CONDITION,
    )

    differences = [levels[t] - levels[t - 1] for t in range(1, len(levels))]
    scores = []
    for k in range(maximum + 1):
        rows = range(maximum, len(differences))
        design = [[1.0, levels[t], *(differences[t - i] for i in range(1, k + 1))] for t in rows]
        fit = ordinary_least_squares(design, [differences[t] for t in rows], CONDITION)
        with localcontext() as context:
            context.prec = 34
            size = Decimal(fit.observations)
            scores.append(
                size * (Decimal(fit.sum_of_squares) / size).ln()
                + Decimal(len(fit.coefficients)) * size.ln()
            )
    assert test.lags == scores.index(min(scores))
    assert test.observations == len(levels) - 1 - test.lags
    assert test.regression.settings["lag_rule"] == "bic"
    assert test.regression.settings["lag_maximum"] == maximum


def test_lag_selection_recovers_the_order_that_generated_the_differences() -> None:
    """Over 30 samples, BIC and the t-statistic rule mostly find one lag; AIC never fewer."""

    found = {LagCriterion.BIC: 0, LagCriterion.T_STATISTIC: 0}
    for seed in range(30):
        levels = _ar_differences(100 + seed, 400)
        for criterion, threshold in ((LagCriterion.BIC, None), (LagCriterion.T_STATISTIC, 2.576)):
            chosen = augmented_dickey_fuller(
                levels,
                deterministic=Deterministic.CONSTANT,
                lags=SelectedLags(criterion, 6, threshold),
                maximum_condition=CONDITION,
            ).lags
            found[criterion] += chosen == 1
        aic = augmented_dickey_fuller(
            levels,
            deterministic=Deterministic.CONSTANT,
            lags=SelectedLags(LagCriterion.AIC, 6, None),
            maximum_condition=CONDITION,
        ).lags
        assert aic >= 1, "with a lag this strong, AIC never underfits"

    assert found[LagCriterion.BIC] >= 24 and found[LagCriterion.T_STATISTIC] >= 24, found


@pytest.mark.parametrize(
    ("constant", "observations", "lags"),
    [(4, 100, 4), (4, 499, 5), (12, 500, 17), (4, 244, 4), (4, 245, 5)],
)
def test_the_schwert_bandwidth_is_exact(constant: int, observations: int, lags: int) -> None:
    """``⌊c (T / 100)^{1/4}⌋``; ``4 (244.14 / 100)^{1/4} = 5`` is the edge."""

    assert SchwertBandwidth(constant).lags(observations) == lags


def test_the_regressions_identity_covers_the_series_and_every_choice() -> None:
    walk = _walk(9, 200)

    def test(values: list[float], lags: int, deterministic: Deterministic) -> str:
        return augmented_dickey_fuller(
            values, deterministic=deterministic, lags=FixedLags(lags), maximum_condition=CONDITION
        ).regression.estimate_id

    base = test(walk, 1, Deterministic.CONSTANT)
    assert base == test(list(walk), 1, Deterministic.CONSTANT)
    assert base != test([*walk[:-1], walk[-1] + 1e-9], 1, Deterministic.CONSTANT)
    assert base != test(walk, 2, Deterministic.CONSTANT)
    assert base != test(walk, 1, Deterministic.CONSTANT_TREND)
    regression = augmented_dickey_fuller(
        walk,
        deterministic=Deterministic.CONSTANT_QUADRATIC_TREND,
        lags=FixedLags(2),
        maximum_condition=CONDITION,
    ).regression
    assert regression.data_id == series_identity(walk)
    assert list(regression.parameters) == sorted(
        ["constant", "trend", "trend_squared", "level_lag", "difference_lag_1", "difference_lag_2"]
    )
    assert regression.numerical_class.identity_bearing


@pytest.mark.parametrize(
    ("make", "match"),
    [
        (lambda: _adf([float(v) for v in range(20)]), "19 observations"),
        (lambda: _adf([*_walk(1, 60), float("inf")]), "not finite"),
        (
            lambda: augmented_dickey_fuller(
                _walk(1, 60),
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(0),
                maximum_condition=0.5,
            ),
            "at least 1",
        ),
        (lambda: SelectedLags(LagCriterion.AIC, 4, 1.96), "takes no threshold"),
        (lambda: SelectedLags(LagCriterion.T_STATISTIC, 4, None), "threshold stated"),
        (lambda: FixedLags(-1), "non-negative"),
        (lambda: SchwertBandwidth(0), "positive integer"),
        (
            lambda: kpss(
                _walk(1, 60),
                deterministic=Deterministic.NONE,
                bandwidth=FixedBandwidth(2),
                maximum_condition=CONDITION,
            ),
            "level stationarity",
        ),
        (
            lambda: kpss(
                [1.0] * 30,
                deterministic=Deterministic.CONSTANT,
                bandwidth=FixedBandwidth(2),
                maximum_condition=CONDITION,
            ),
            "long-run variance is zero",
        ),
    ],
)
def test_what_a_test_cannot_take_is_refused(make: object, match: str) -> None:
    with pytest.raises(EconometricsInputError, match=match):
        make()  # type: ignore[operator]
