"""Engle and Granger's test of no cointegration (v4.3, ADR-0074, WS-07).

* A pair built cointegrated -- ``y = 2 + 1.5 x + u``, ``u`` a stationary AR(1)
  -- is found so, and its hedge ratio recovered within its standard errors.
* Two independent random walks are not, and over many pairs the test rejects at
  its nominal rate: MacKinnon's critical values for two series are right.
* Both steps are held to exact arithmetic: the cointegrating regression's
  coefficients, and the residuals' Dickey-Fuller t-statistic with no constant.
* The p-value is MacKinnon's for up to six series, and ``None`` beyond, where
  only the critical values are published; every refusal is held.
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

from alphalab.common.linalg import IllConditionedError
from alphalab.econometrics.cointegration import engle_granger
from alphalab.econometrics.critical_values import Deterministic, mackinnon_critical_values
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import system_identity
from alphalab.econometrics.unit_root import FixedLags, LagCriterion, SelectedLags

CONDITION = 1e10


def _walk(draws: random.Random, count: int) -> list[float]:
    values = [0.0]
    for _ in range(count - 1):
        values.append(values[-1] + draws.gauss(0.0, 1.0))
    return values


def _pair(seed: int, count: int) -> tuple[list[float], list[float]]:
    """``y = 2 + 1.5 x + u``: ``x`` a random walk, ``u`` an AR(1) with coefficient 0.5."""

    draws = random.Random(seed)
    x = _walk(draws, count)
    noise = [0.0]
    for _ in range(count - 1):
        noise.append(0.5 * noise[-1] + draws.gauss(0.0, 1.0))
    return [2.0 + 1.5 * a + b for a, b in zip(x, noise, strict=True)], x


def test_a_cointegrated_pair_is_found_and_its_hedge_ratio_recovered() -> None:
    y, x = _pair(5, 500)

    test = engle_granger(
        y,
        [x],
        deterministic=Deterministic.CONSTANT,
        lags=SelectedLags(LagCriterion.BIC, 6, None),
        maximum_condition=CONDITION,
    )

    assert test.variables == 2
    assert test.statistic < test.critical_values.values[0.01]
    assert test.p_value is not None and test.p_value.value < 0.001
    ratio = test.cointegrating.parameters["beta_1"]
    error = test.cointegrating.standard_errors["beta_1"]
    assert abs(ratio - 1.5) < 4 * error and abs(ratio - 1.5) < 0.05
    assert (
        test.critical_values.values
        == mackinnon_critical_values(Deterministic.CONSTANT, 2, test.observations).values
    )


def test_independent_walks_are_not_cointegrated_and_the_test_keeps_its_size() -> None:
    """150 pairs of independent walks of 100: rejections at 5% within three deviations."""

    rejections = 0
    for seed in range(150):
        draws = random.Random(9000 + seed)
        y, x = _walk(draws, 100), _walk(draws, 100)
        test = engle_granger(
            y,
            [x],
            deterministic=Deterministic.CONSTANT,
            lags=FixedLags(0),
            maximum_condition=CONDITION,
        )
        rejections += test.statistic < test.critical_values.values[0.05]

    deviation = (0.05 * 0.95 / 150) ** 0.5
    assert 0.05 - 3 * deviation <= rejections / 150 <= 0.05 + 3 * deviation


def test_both_steps_are_the_exact_arithmetic_answers() -> None:
    y = [
        3.0,
        4.5,
        4.0,
        6.5,
        7.0,
        6.0,
        8.5,
        9.0,
        8.0,
        10.5,
        11.5,
        10.0,
        12.5,
        13.0,
        12.0,
        14.5,
        15.5,
        14.0,
        16.5,
        17.0,
        16.0,
        18.5,
        19.5,
        18.0,
    ]
    x = [
        1.0,
        2.0,
        2.0,
        3.0,
        4.0,
        3.5,
        5.0,
        5.5,
        4.5,
        6.0,
        7.0,
        6.0,
        7.5,
        8.0,
        7.0,
        8.5,
        9.5,
        8.5,
        10.0,
        10.5,
        9.5,
        11.0,
        12.0,
        11.0,
    ]
    test = engle_granger(
        y, [x], deterministic=Deterministic.CONSTANT, lags=FixedLags(0), maximum_condition=CONDITION
    )

    fy, fx = [Fraction(v) for v in y], [Fraction(v) for v in x]
    n = len(fy)
    sx, sy = sum(fx, Fraction(0)), sum(fy, Fraction(0))
    sxx = sum((a * a for a in fx), Fraction(0))
    sxy = sum((a * b for a, b in zip(fx, fy, strict=True)), Fraction(0))
    beta = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    alpha = (sy - beta * sx) / n
    residuals = [b - alpha - beta * a for a, b in zip(fx, fy, strict=True)]
    lagged = residuals[:-1]
    target = [residuals[t] - residuals[t - 1] for t in range(1, n)]
    slm = sum((v * v for v in lagged), Fraction(0))
    rho = sum((a * b for a, b in zip(lagged, target, strict=True)), Fraction(0)) / slm
    m = len(target)
    s2 = sum(((v - rho * u) ** 2 for u, v in zip(lagged, target, strict=True)), Fraction(0)) / (
        m - 1
    )
    t_squared = rho * rho * slm / s2

    assert test.cointegrating.parameters["beta_1"] == pytest.approx(float(beta), rel=1e-12)
    assert test.cointegrating.parameters["constant"] == pytest.approx(float(alpha), rel=1e-12)
    assert test.observations == m
    assert test.statistic < 0
    assert test.statistic**2 == pytest.approx(float(t_squared), rel=1e-11)


def test_beyond_six_series_the_critical_values_stand_without_a_p_value() -> None:
    draws = random.Random(21)
    series = [_walk(draws, 200) for _ in range(7)]

    test = engle_granger(
        series[0],
        series[1:],
        deterministic=Deterministic.CONSTANT_TREND,
        lags=FixedLags(1),
        maximum_condition=CONDITION,
    )

    assert test.variables == 7 and test.p_value is None
    assert (
        test.critical_values.values
        == mackinnon_critical_values(Deterministic.CONSTANT_TREND, 7, test.observations).values
    )
    assert len([name for name in test.cointegrating.parameters if name.startswith("beta_")]) == 6


def test_the_cointegrating_regression_is_identified_by_its_series_in_order() -> None:
    y, x = _pair(8, 120)

    def identity(dependent: list[float], other: list[float]) -> str:
        return engle_granger(
            dependent,
            [other],
            deterministic=Deterministic.CONSTANT,
            lags=FixedLags(0),
            maximum_condition=CONDITION,
        ).cointegrating.data_id

    assert identity(y, x) == system_identity([y, x])
    assert identity(y, x) != identity(x, y), "y on x is not x on y"


@pytest.mark.parametrize(
    ("make", "error", "match"),
    [
        (
            lambda y, x: engle_granger(
                y,
                [x],
                deterministic=Deterministic.NONE,
                lags=FixedLags(0),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            "a constant at least",
        ),
        (
            lambda y, x: engle_granger(
                y,
                [],
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(0),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            "1 to 11 regressors",
        ),
        (
            lambda y, x: engle_granger(
                y,
                [x] * 12,
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(0),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            "1 to 11 regressors",
        ),
        (
            lambda y, x: engle_granger(
                y,
                [x[:-1]],
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(0),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            "over one set of instants",
        ),
        (
            lambda y, x: engle_granger(
                y,
                [[*x[:-1], float("nan")]],
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(0),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            "Regressor 1 holds nan at 59: not finite",
        ),
        (
            lambda y, x: engle_granger(
                y[:20],
                [x[:20]],
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(0),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            "19 observations",
        ),
        (
            lambda y, x: engle_granger(
                y,
                [x, [2.0 * v for v in x]],
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(0),
                maximum_condition=CONDITION,
            ),
            IllConditionedError,
            "beyond the stated maximum",
        ),
    ],
)
def test_what_engle_granger_cannot_take_is_refused(
    make: object, error: type[Exception], match: str
) -> None:
    y, x = _pair(3, 60)
    with pytest.raises(error, match=match):
        make(y, x)  # type: ignore[operator]
