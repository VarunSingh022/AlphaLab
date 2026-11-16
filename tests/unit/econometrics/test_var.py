"""Vector autoregressions (v4.3, ADR-0074, WS-07).

* **Known coefficients are recovered**, within their standard errors.
* **Each equation is exact**: the rational least-squares solution on its design.
* **The order is found**: BIC chooses a VAR(2)'s two lags, every candidate
  fitted on the maximum's sample.
* **Every refusal is held**, and the fit is identified by its system and choices.
"""

from __future__ import annotations

import math
import random
from fractions import Fraction

import pytest

from alphalab.common.linalg import IllConditionedError, cholesky, least_squares
from alphalab.econometrics.critical_values import Deterministic
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import system_identity
from alphalab.econometrics.unit_root import FixedLags, LagCriterion, SelectedLags
from alphalab.econometrics.var import MINIMUM_VAR_OBSERVATIONS, fit_var

CONDITION = 1e10


def _simulate(
    draws: random.Random, count: int, matrices: list[list[list[float]]], constant: list[float]
) -> list[list[float]]:
    size = len(constant)
    rows = [[0.0] * size for _ in matrices]
    for _ in range(count + 200):
        values = list(constant)
        for lag, matrix in enumerate(matrices, start=1):
            previous = rows[-lag]
            for i in range(size):
                values[i] += sum(matrix[i][k] * previous[k] for k in range(size))
        rows.append([v + draws.gauss(0.0, 1.0) for v in values])
    kept = rows[-count:]
    return [[row[i] for row in kept] for i in range(size)]


def test_known_coefficients_are_recovered_within_their_standard_errors() -> None:
    a = [[0.5, 0.1], [-0.2, 0.3]]
    series = _simulate(random.Random(1), 3000, [a], [0.2, -0.1])
    fit = fit_var(
        series, deterministic=Deterministic.CONSTANT, lags=FixedLags(1), maximum_condition=CONDITION
    )

    assert fit.lags == 1 and fit.observations == 2999
    for i, equation in enumerate(fit.equations):
        for k in range(2):
            name = f"phi_1_series_{k + 1}"
            assert abs(equation.parameters[name] - a[i][k]) < 3 * equation.standard_errors[name]
    sigma = fit.covariance.parameters
    assert sigma["sigma_1_1"] == pytest.approx(1.0, abs=0.08)
    assert sigma["sigma_1_2"] == sigma["sigma_2_1"]


def _dyadic(count: int) -> list[list[float]]:
    state, first, second = 5, [0.0], [0.0]
    for _ in range(count - 1):
        state = (1103515245 * state + 12345) % 2**31
        first.append(first[-1] / 2 + ((state >> 8) % 9 - 4) / 4)
        state = (1103515245 * state + 12345) % 2**31
        second.append(second[-1] / 4 + first[-2] / 2 + ((state >> 8) % 7 - 3) / 8)
    return [first, second]


def test_each_equation_is_the_exact_least_squares_solution() -> None:
    series = _dyadic(40)
    fit = fit_var(
        series, deterministic=Deterministic.CONSTANT, lags=FixedLags(2), maximum_condition=CONDITION
    )

    rows = range(2, 40)
    design = [[Fraction(1), *(Fraction(x[t - i]) for i in (1, 2) for x in series)] for t in rows]
    width = len(design[0])
    for equation, target_series in zip(fit.equations, series, strict=True):
        target = [Fraction(target_series[t]) for t in rows]
        augmented = [
            [
                *(sum((r[i] * r[j] for r in design), Fraction(0)) for j in range(width)),
                sum((r[i] * y for r, y in zip(design, target, strict=True)), Fraction(0)),
            ]
            for i in range(width)
        ]
        for c in range(width):
            for r in range(width):
                if r != c:
                    factor = augmented[r][c] / augmented[c][c]
                    augmented[r] = [
                        u - factor * v for u, v in zip(augmented[r], augmented[c], strict=True)
                    ]
        exact = [augmented[i][width] / augmented[i][i] for i in range(width)]
        names = ["constant", "phi_1_series_1", "phi_1_series_2", "phi_2_series_1", "phi_2_series_2"]
        for name, value in zip(names, exact, strict=True):
            assert equation.parameters[name] == pytest.approx(float(value), rel=1e-11, abs=1e-14)


def test_bic_finds_a_var2s_order() -> None:
    first = [[0.4, 0.1], [0.0, 0.3]]
    second = [[0.3, 0.0], [0.2, -0.3]]
    series = _simulate(random.Random(8), 2500, [first, second], [0.0, 0.0])

    fit = fit_var(
        series,
        deterministic=Deterministic.CONSTANT,
        lags=SelectedLags(LagCriterion.BIC, 6, None),
        maximum_condition=CONDITION,
    )

    assert fit.lags == 2 and fit.observations == 2498, "the chosen order fitted on all it leaves"
    settings = fit.equations[0].settings
    assert settings["lags"] == 2 and settings["lag_maximum"] == 6


def _criterion(series: list[list[float]], lags: int, start: int, rule: LagCriterion) -> float:
    """``T ln det(Sigma) + penalty`` for ``lags`` lags, regressed over the rows from ``start``."""

    count, length = len(series), len(series[0])
    rows = range(start, length)
    design = [
        [1.0, *(series[k][t - lag] for lag in range(1, lags + 1) for k in range(count))]
        for t in rows
    ]
    residuals = [
        least_squares(design, [series[i][t] for t in rows], CONDITION).residuals
        for i in range(count)
    ]
    size = len(rows)
    sigma = [
        [
            math.fsum(a * b for a, b in zip(residuals[i], residuals[k], strict=True)) / size
            for k in range(count)
        ]
        for i in range(count)
    ]
    log_determinant = math.fsum(math.log(pivot) for pivot in cholesky(sigma).pivots)
    width = len(design[0]) * count
    penalty = 2 * width if rule is LagCriterion.AIC else width * math.log(size)
    return size * log_determinant + penalty


def test_each_criterion_chooses_its_least_value_over_the_maximums_sample() -> None:
    """A series where AIC and BIC disagree, and where each candidate on its own sample would too."""

    series = _simulate(
        random.Random(0),
        60,
        [[[0.5, 0.1], [0.0, 0.4]], [[0.0, 0.0], [0.0, 0.0]], [[0.15, 0.0], [0.1, -0.15]]],
        [0.0, 0.0],
    )
    most = 5
    chosen = {}
    for rule in (LagCriterion.AIC, LagCriterion.BIC):
        by_hand = min(range(1, most + 1), key=lambda p: _criterion(series, p, most, rule))
        fit = fit_var(
            series,
            deterministic=Deterministic.CONSTANT,
            lags=SelectedLags(rule, most, None),
            maximum_condition=CONDITION,
        )
        assert fit.lags == by_hand
        chosen[rule] = by_hand
    own_sample = min(range(1, most + 1), key=lambda p: _criterion(series, p, p, LagCriterion.AIC))
    assert chosen[LagCriterion.AIC] != chosen[LagCriterion.BIC]
    assert own_sample != chosen[LagCriterion.AIC]


def test_the_fit_is_identified_by_its_system_and_every_choice() -> None:
    series = _dyadic(60)
    fit = fit_var(
        series,
        deterministic=Deterministic.CONSTANT_TREND,
        lags=FixedLags(1),
        maximum_condition=CONDITION,
    )
    for estimate in (*fit.equations, fit.covariance):
        assert estimate.data_id == system_identity(series)
        assert estimate.settings["deterministic"] == "ct"
        assert estimate.numerical_class.identity_bearing
    assert [e.settings["equation"] for e in fit.equations] == [1, 2]
    assert {"constant", "trend"} <= set(fit.equations[0].parameters)


@pytest.mark.parametrize(
    ("make", "error", "match"),
    [
        (
            lambda s: fit_var(
                s,
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(0),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            "one lag at least",
        ),
        (
            lambda s: fit_var(
                s,
                deterministic=Deterministic.CONSTANT,
                lags=SelectedLags(LagCriterion.T_STATISTIC, 4, 1.96),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            "AIC or BIC",
        ),
        (
            lambda s: fit_var(
                [s[0], s[1][:-1]],
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(1),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            "one set of instants",
        ),
        (
            lambda s: fit_var(
                [s[0][:20], s[1][:20]],
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(1),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            f"{MINIMUM_VAR_OBSERVATIONS} at least",
        ),
        (
            lambda s: fit_var(
                [],
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(1),
                maximum_condition=CONDITION,
            ),
            EconometricsInputError,
            "one series at least",
        ),
        (
            lambda s: fit_var(
                [s[0], s[0]],
                deterministic=Deterministic.CONSTANT,
                lags=FixedLags(1),
                maximum_condition=CONDITION,
            ),
            IllConditionedError,
            "beyond the stated maximum",
        ),
    ],
)
def test_what_a_var_cannot_take_is_refused(
    make: object, error: type[Exception], match: str
) -> None:
    series = _dyadic(60)
    with pytest.raises(error, match=match):
        make(series)  # type: ignore[operator]
