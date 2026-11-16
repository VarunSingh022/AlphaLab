"""Cointegration: Engle and Granger's two-step test (v4.3, ADR-0074, WS-07).

Two or more series that each have a unit root are cointegrated when some linear
combination of them does not: the combination's deviations revert. Engle and
Granger (1987) test it in two steps.

1. **The cointegrating regression.** ``y`` on its deterministic terms and the
   other series, by least squares. Its coefficients on the other series are the
   combination -- for a pair, the hedge ratio.
2. **The residuals' unit-root test.** An augmented Dickey-Fuller regression on
   the residuals, with no deterministic terms: step 1 already removed them. Its
   t-statistic is the test statistic.

Under the null of no cointegration the residuals have a unit root. Because the
combination was estimated to make them as stationary as possible, the statistic
does not follow the Dickey-Fuller distribution: MacKinnon tabulates it by ``N``,
the series in the cointegrating regression, and by the deterministic terms of
step 1 (:mod:`~alphalab.econometrics.critical_values`). The no-constant case is
published for ``N = 1`` only, so step 1 always has a constant at least.

Choices and classes are the unit-root tests' (:mod:`~alphalab.econometrics.unit_root`):
every choice stated, every statistic class 2, MacKinnon's p-value class 3. The
p-value approximation is published for ``N`` up to 6; for 7 to 12 series the
critical values are, and the p-value is ``None``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from alphalab.common.estimate import Estimate, NumericalClass
from alphalab.econometrics._regression import ordinary_least_squares
from alphalab.econometrics.critical_values import (
    CriticalValues,
    Deterministic,
    PValue,
    mackinnon_critical_values,
    mackinnon_p_value,
)
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import require_series, series_identity, system_identity
from alphalab.econometrics.unit_root import (
    FixedLags,
    LagRule,
    SelectedLags,
    _deterministic_columns,
    _deterministic_names,
    _dickey_fuller,
    _lag_settings,
    _require_condition,
    _require_deterministic,
    _require_dickey_fuller_sample,
    _select,
)

__all__ = [
    "ENGLE_GRANGER_METHOD",
    "ENGLE_GRANGER_RESIDUAL_METHOD",
    "CointegrationTest",
    "engle_granger",
]

#: The cointegrating regression's method scheme.
ENGLE_GRANGER_METHOD: Final = "alphalab.econometrics.cointegration.engle_granger/ols.v1"
#: The residuals' Dickey-Fuller regression's method scheme.
ENGLE_GRANGER_RESIDUAL_METHOD: Final = (
    "alphalab.econometrics.cointegration.engle_granger_residuals/ols.v1"
)

#: The most series MacKinnon (2010) tabulates.
_MOST_VARIABLES: Final = 12
#: The most series MacKinnon (1994) approximates p-values for.
_MOST_P_VALUE_VARIABLES: Final = 6


@dataclass(frozen=True, slots=True)
class CointegrationTest:
    """Engle and Granger's test of no cointegration.

    Attributes:
        statistic: The residuals' augmented Dickey-Fuller t-statistic.
        lags: The lagged differences in the residuals' regression.
        observations: ``T``, the residuals' regression's observations.
        variables: ``N``, the series in the cointegrating regression.
        critical_values: MacKinnon (2010)'s, for ``N`` and ``T``.
        p_value: MacKinnon (1994)'s, or ``None`` for more than six series.
        cointegrating: The cointegrating regression's estimate. Its
            coefficients ``beta_1`` onward are the combination: for a pair,
            ``beta_1`` is the hedge ratio.
        residual_regression: The residuals' Dickey-Fuller regression's estimate.
    """

    statistic: float
    lags: int
    observations: int
    variables: int
    critical_values: CriticalValues
    p_value: PValue | None
    cointegrating: Estimate
    residual_regression: Estimate


def engle_granger(
    dependent: Sequence[float],
    regressors: Sequence[Sequence[float]],
    *,
    deterministic: Deterministic,
    lags: LagRule,
    maximum_condition: float,
) -> CointegrationTest:
    """Engle and Granger's test that ``dependent`` and ``regressors`` are not cointegrated.

    Args:
        dependent: The series regressed on the others, in time order.
        regressors: One to eleven other series, each as long as ``dependent``.
        deterministic: The cointegrating regression's deterministic terms: a
            constant at least.
        lags: The residuals' regression's lagged differences, fixed or selected.
        maximum_condition: The largest condition number a regression may have.

    Raises:
        EconometricsInputError: If a value is not finite, the series differ in
            length, there are no regressors or more than eleven, the
            deterministic terms are none, a choice is malformed, or the
            residuals' regression would have fewer than 20 observations.
        IllConditionedError: If a regression's design is too ill-conditioned:
            regressors that are, or nearly are, multiples of each other.
    """

    _require_deterministic(deterministic)
    _require_condition(maximum_condition)
    if deterministic is Deterministic.NONE:
        raise EconometricsInputError(
            "MacKinnon publishes the no-constant case for one series only: an Engle-Granger "
            "regression states a constant at least."
        )
    if not isinstance(lags, FixedLags | SelectedLags):
        raise EconometricsInputError(f"lags must be FixedLags or SelectedLags, got {lags!r}.")
    count = len(regressors)
    if not 1 <= count <= _MOST_VARIABLES - 1:
        raise EconometricsInputError(
            f"An Engle-Granger test takes 1 to {_MOST_VARIABLES - 1} regressors, the "
            f"series MacKinnon tabulates, got {count}."
        )
    y = require_series(dependent, 3, "The dependent series")
    xs = [require_series(each, 3, f"Regressor {i + 1}") for i, each in enumerate(regressors)]
    for i, each in enumerate(xs):
        if len(each) != len(y):
            raise EconometricsInputError(
                f"Regressor {i + 1} has {len(each)} values and the dependent series {len(y)}: "
                "a cointegrating regression is over one set of instants."
            )
    widest = lags.count if isinstance(lags, FixedLags) else lags.maximum
    _require_dickey_fuller_sample(len(y) - 1 - widest, "The residuals' regression")

    terms = _deterministic_columns(deterministic, len(y))
    design = [[*terms[t], *(x[t] for x in xs)] for t in range(len(y))]
    fit = ordinary_least_squares(design, y, maximum_condition)
    names = [*_deterministic_names(deterministic), *(f"beta_{i}" for i in range(1, count + 1))]
    cointegrating = Estimate(
        method=ENGLE_GRANGER_METHOD,
        data_id=system_identity([y, *xs]),
        parameters=dict(zip(names, fit.coefficients, strict=True)),
        standard_errors=dict(zip(names, fit.standard_errors, strict=True)),
        settings={
            "deterministic": deterministic.value,
            "maximum_condition": float(maximum_condition),
            "observations": fit.observations,
            "regressors": count,
        },
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
    )

    residuals = fit.residuals
    differences = tuple(residuals[t] - residuals[t - 1] for t in range(1, len(residuals)))
    shifted = (0.0, *residuals[:-1])
    padded = (0.0, *differences)
    chosen = (
        lags.count
        if isinstance(lags, FixedLags)
        else _select(shifted, padded, Deterministic.NONE, lags, maximum_condition)
    )
    second = _dickey_fuller(
        shifted, padded, Deterministic.NONE, chosen, chosen + 1, maximum_condition
    )
    statistic = second.coefficients[0] / second.standard_errors[0]
    residual_names = ["level_lag", *(f"difference_lag_{i}" for i in range(1, chosen + 1))]
    residual_regression = Estimate(
        method=ENGLE_GRANGER_RESIDUAL_METHOD,
        data_id=series_identity(residuals),
        parameters=dict(zip(residual_names, second.coefficients, strict=True)),
        standard_errors=dict(zip(residual_names, second.standard_errors, strict=True)),
        settings={
            "lags": chosen,
            "maximum_condition": float(maximum_condition),
            "observations": second.observations,
            **_lag_settings(lags),
        },
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
    )
    variables = count + 1
    return CointegrationTest(
        statistic=statistic,
        lags=chosen,
        observations=second.observations,
        variables=variables,
        critical_values=mackinnon_critical_values(deterministic, variables, second.observations),
        p_value=(
            mackinnon_p_value(statistic, deterministic, variables)
            if variables <= _MOST_P_VALUE_VARIABLES
            else None
        ),
        cointegrating=cointegrating,
        residual_regression=residual_regression,
    )
