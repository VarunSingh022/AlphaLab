"""Unit-root and stationarity tests: augmented Dickey-Fuller, Phillips-Perron and KPSS.

ADR-0074 sections 2, 4 and 6. Each test takes a series and every choice it
turns on, and returns a :class:`UnitRootTest`: the statistic, the critical
values with their source, the p-value with how it was computed, and the
:class:`~alphalab.common.estimate.Estimate` of the regression the statistic
came from.

======================= =================================== ==================
Test                    Null hypothesis                     Rejects when
======================= =================================== ==================
augmented Dickey-Fuller a unit root                         the statistic is
                                                            below the critical
                                                            value
Phillips-Perron         a unit root                         the same
KPSS                    stationarity, about a level or a    the statistic is
                        trend                               above it
======================= =================================== ==================

The regressions
---------------
With ``Δy_t = y_t - y_{t-1}``, the Dickey-Fuller regression of ``T``
observations is ``Δy_t = d_t + rho y_{t-1} + Σ_{i=1..k} φ_i Δy_{t-i} + ε_t``,
``d_t`` the deterministic terms. The ADF statistic is ``rho-hat`` over its standard
error. Phillips-Perron runs it with no lags and corrects the statistic for
serial correlation by the residuals' long-run variance (Phillips and Perron,
1988; Hamilton, 1994, equation 17.6.12). KPSS regresses the series on its
deterministic terms, and divides the mean square of the residuals' partial sums
by ``T`` times their long-run variance (Kwiatkowski, Phillips, Schmidt and Shin,
1992).

The trend is ``t / T`` and its square, for ``t = 1..T``. Rescaling a
deterministic term changes only its own coefficient, never the statistic, and
keeps the design well conditioned.

Choices, all stated
-------------------
* **Deterministic terms:** :class:`~alphalab.econometrics.critical_values.Deterministic`.
* **Lags (ADF):** :class:`FixedLags`, or :class:`SelectedLags` by AIC, BIC or
  the last lag's t-statistic against a stated threshold, up to a stated maximum.
  Every candidate is fitted on the same sample, the one the maximum leaves, so
  their criteria compare. The chosen lag is then fitted on all the data it
  leaves.
* **Bandwidth (Phillips-Perron, KPSS):** :class:`FixedBandwidth`, or
  :class:`SchwertBandwidth`, ``⌊c (T / 100)^{1/4}⌋``.
* **The largest condition number** a regression may have.

Numerical classes (ADR-0058)
----------------------------
Every statistic is least squares, sums and square roots: class 2, in the
regression's identity. AIC and BIC compare by ``Decimal``'s correctly rounded
logarithm, and the Schwert bandwidth is exact integer arithmetic, so a chosen
lag or bandwidth is the same on every host. MacKinnon's p-values pass through
the normal distribution, class 3, and are reported beside the identity.

Refusals
--------
A regression of fewer than 20 observations is refused for the Dickey-Fuller
tests: MacKinnon's response surfaces were fitted from samples of 20 or more,
and a smaller one would read them where they were never estimated.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from typing import Final

from alphalab.common.estimate import Estimate, NumericalClass, Setting
from alphalab.econometrics._regression import (
    OrdinaryLeastSquares,
    long_run_variance,
    ordinary_least_squares,
)
from alphalab.econometrics.critical_values import (
    CriticalValues,
    Deterministic,
    PValue,
    kpss_critical_values,
    kpss_p_value,
    mackinnon_critical_values,
    mackinnon_p_value,
)
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import require_series, series_identity

__all__ = [
    "ADF_METHOD",
    "KPSS_METHOD",
    "MINIMUM_DICKEY_FULLER_OBSERVATIONS",
    "PHILLIPS_PERRON_METHOD",
    "Bandwidth",
    "FixedBandwidth",
    "FixedLags",
    "LagCriterion",
    "LagRule",
    "Null",
    "SchwertBandwidth",
    "SelectedLags",
    "UnitRootTest",
    "augmented_dickey_fuller",
    "kpss",
    "phillips_perron",
]

#: The augmented Dickey-Fuller regression's method scheme.
ADF_METHOD: Final = "alphalab.econometrics.unit_root.augmented_dickey_fuller/ols.v1"
#: The Phillips-Perron regression's method scheme.
PHILLIPS_PERRON_METHOD: Final = "alphalab.econometrics.unit_root.phillips_perron/ols.v1"
#: The KPSS detrending regression's method scheme.
KPSS_METHOD: Final = "alphalab.econometrics.unit_root.kpss/ols.v1"

#: The fewest observations a Dickey-Fuller regression may have: MacKinnon's smallest ``T``.
MINIMUM_DICKEY_FULLER_OBSERVATIONS: Final = 20


class Null(Enum):
    """What a test's null hypothesis states."""

    #: The series has a unit root (ADF, Phillips-Perron).
    UNIT_ROOT = "unit_root"
    #: The series is stationary about its deterministic terms (KPSS).
    STATIONARITY = "stationarity"


@dataclass(frozen=True, slots=True)
class FixedLags:
    """A fixed number of lagged differences in the ADF regression."""

    count: int

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 0:
            raise EconometricsInputError(
                f"FixedLags.count must be a non-negative integer, got {self.count!r}."
            )


class LagCriterion(Enum):
    """How :class:`SelectedLags` chooses."""

    #: Akaike's: ``T ln(SSR / T) + 2 p``, the least.
    AIC = "aic"
    #: Schwarz's: ``T ln(SSR / T) + p ln T``, the least.
    BIC = "bic"
    #: From the maximum down, the first lag whose t-statistic reaches the threshold.
    T_STATISTIC = "t_statistic"


@dataclass(frozen=True, slots=True)
class SelectedLags:
    """Lags chosen by a criterion, from 0 to ``maximum``.

    Attributes:
        criterion: The criterion.
        maximum: The most lags considered.
        threshold: The ``|t|`` the last lag must reach, for
            :attr:`LagCriterion.T_STATISTIC` only; ``None`` otherwise.
    """

    criterion: LagCriterion
    maximum: int
    threshold: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.criterion, LagCriterion):
            raise EconometricsInputError(
                f"SelectedLags.criterion must be a LagCriterion, got {self.criterion!r}."
            )
        if isinstance(self.maximum, bool) or not isinstance(self.maximum, int) or self.maximum < 0:
            raise EconometricsInputError(
                f"SelectedLags.maximum must be a non-negative integer, got {self.maximum!r}."
            )
        if self.criterion is LagCriterion.T_STATISTIC:
            threshold = self.threshold
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, int | float)
                or not math.isfinite(threshold)
                or threshold <= 0.0
            ):
                raise EconometricsInputError(
                    "The t-statistic criterion needs its threshold stated, a positive number; "
                    f"got {threshold!r}."
                )
        elif self.threshold is not None:
            raise EconometricsInputError(
                f"{self.criterion.name} takes no threshold; got {self.threshold!r}."
            )


#: How an ADF regression's lags are chosen.
LagRule = FixedLags | SelectedLags


@dataclass(frozen=True, slots=True)
class FixedBandwidth:
    """A fixed number of lags in the long-run variance."""

    lags: int

    def __post_init__(self) -> None:
        if isinstance(self.lags, bool) or not isinstance(self.lags, int) or self.lags < 0:
            raise EconometricsInputError(
                f"FixedBandwidth.lags must be a non-negative integer, got {self.lags!r}."
            )


@dataclass(frozen=True, slots=True)
class SchwertBandwidth:
    """``⌊c (T / 100)^{1/4}⌋`` lags (Schwert, 1989; KPSS's ``l4`` and ``l12``), exactly.

    The largest integer ``m`` with ``100 m⁴ <= c⁴ T``: integer arithmetic, so the
    same on every host.
    """

    constant: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.constant, bool)
            or not isinstance(self.constant, int)
            or self.constant < 1
        ):
            raise EconometricsInputError(
                f"SchwertBandwidth.constant must be a positive integer, got {self.constant!r}."
            )

    def lags(self, observations: int) -> int:
        """The bandwidth for ``observations``."""

        bound = self.constant**4 * observations
        lags = 0
        while 100 * (lags + 1) ** 4 <= bound:
            lags += 1
        return lags


#: How a long-run variance's lags are chosen.
Bandwidth = FixedBandwidth | SchwertBandwidth


@dataclass(frozen=True, slots=True)
class UnitRootTest:
    """A unit-root or stationarity test's result.

    Attributes:
        null: What the null hypothesis states.
        statistic: The test statistic.
        lags: The lagged differences (ADF) or the long-run variance's lags
            (Phillips-Perron, KPSS) the statistic used.
        observations: ``T``, the observations in the test regression.
        critical_values: The critical values, with their source.
        p_value: The p-value, with its source and whether it is a bound.
        regression: The regression's estimate. Its identity covers the series,
            every choice and, being class 2, the fitted values.
    """

    null: Null
    statistic: float
    lags: int
    observations: int
    critical_values: CriticalValues
    p_value: PValue
    regression: Estimate


def _deterministic_columns(deterministic: Deterministic, observations: int) -> list[list[float]]:
    """Each row's deterministic terms, the trend ``t / T`` for ``t = 1..T``."""

    rows: list[list[float]] = []
    for t in range(1, observations + 1):
        trend = t / observations
        if deterministic is Deterministic.NONE:
            rows.append([])
        elif deterministic is Deterministic.CONSTANT:
            rows.append([1.0])
        elif deterministic is Deterministic.CONSTANT_TREND:
            rows.append([1.0, trend])
        else:
            rows.append([1.0, trend, trend * trend])
    return rows


def _deterministic_names(deterministic: Deterministic) -> list[str]:
    return {
        Deterministic.NONE: [],
        Deterministic.CONSTANT: ["constant"],
        Deterministic.CONSTANT_TREND: ["constant", "trend"],
        Deterministic.CONSTANT_QUADRATIC_TREND: ["constant", "trend", "trend_squared"],
    }[deterministic]


def _dickey_fuller(
    levels: tuple[float, ...],
    differences: tuple[float, ...],
    deterministic: Deterministic,
    lags: int,
    first: int,
    maximum_condition: float,
) -> OrdinaryLeastSquares:
    """The DF regression with ``lags`` lagged differences, over ``differences[first:]``."""

    observations = len(differences) - first
    terms = _deterministic_columns(deterministic, observations)
    design = []
    for row, index in enumerate(range(first, len(differences))):
        lagged = [differences[index - i] for i in range(1, lags + 1)]
        design.append([*terms[row], levels[index], *lagged])
    return ordinary_least_squares(design, differences[first:], maximum_condition)


def _require_condition(maximum_condition: float) -> None:
    if (
        isinstance(maximum_condition, bool)
        or not isinstance(maximum_condition, int | float)
        or not math.isfinite(maximum_condition)
        or maximum_condition < 1.0
    ):
        raise EconometricsInputError(
            f"maximum_condition must be a finite number of at least 1, got {maximum_condition!r}."
        )


def _require_deterministic(deterministic: Deterministic) -> None:
    if not isinstance(deterministic, Deterministic):
        raise EconometricsInputError(
            f"deterministic must be a Deterministic, got {deterministic!r}."
        )


def _criterion(fit: OrdinaryLeastSquares, criterion: LagCriterion) -> Decimal:
    """AIC or BIC by ``Decimal``'s correctly rounded logarithm: the same on every host."""

    with localcontext() as context:
        context.prec = 34
        size = Decimal(fit.observations)
        mean_square = Decimal(fit.sum_of_squares) / size
        if mean_square == 0:
            raise EconometricsInputError(
                "A candidate regression fits exactly: its information criterion is undefined."
            )
        parameters = Decimal(len(fit.coefficients))
        penalty = 2 * parameters if criterion is LagCriterion.AIC else parameters * size.ln()
        return size * mean_square.ln() + penalty


def _select(
    levels: tuple[float, ...],
    differences: tuple[float, ...],
    deterministic: Deterministic,
    rule: SelectedLags,
    maximum_condition: float,
) -> int:
    """The lag ``rule`` chooses, every candidate fitted on the maximum's sample.

    ``differences[0]`` holds no difference, so the maximum's sample starts at
    ``maximum + 1``.
    """

    first = rule.maximum + 1
    fits = [
        _dickey_fuller(levels, differences, deterministic, k, first, maximum_condition)
        for k in range(rule.maximum + 1)
    ]
    if rule.criterion is LagCriterion.T_STATISTIC:
        assert rule.threshold is not None
        for k in range(rule.maximum, 0, -1):
            fit = fits[k]
            t = fit.coefficients[-1] / fit.standard_errors[-1]
            if abs(t) >= rule.threshold:
                return k
        return 0
    scores = [_criterion(fit, rule.criterion) for fit in fits]
    return scores.index(min(scores))


def _lag_settings(rule: LagRule) -> dict[str, Setting]:
    if isinstance(rule, FixedLags):
        return {"lag_rule": "fixed"}
    settings: dict[str, Setting] = {
        "lag_rule": rule.criterion.value,
        "lag_maximum": rule.maximum,
    }
    if rule.threshold is not None:
        settings["lag_threshold"] = float(rule.threshold)
    return settings


def _require_dickey_fuller_sample(observations: int, what: str) -> None:
    if observations < MINIMUM_DICKEY_FULLER_OBSERVATIONS:
        raise EconometricsInputError(
            f"{what} has {observations} observations; MacKinnon's response surfaces were "
            f"fitted from {MINIMUM_DICKEY_FULLER_OBSERVATIONS} or more, so a smaller sample "
            "would read them where they were never estimated."
        )


def augmented_dickey_fuller(
    values: Sequence[float],
    *,
    deterministic: Deterministic,
    lags: LagRule,
    maximum_condition: float,
) -> UnitRootTest:
    """The augmented Dickey-Fuller test of a unit root in ``values``.

    Args:
        values: The series, in time order.
        deterministic: The regression's deterministic terms.
        lags: The lagged differences: fixed, or selected by a criterion.
        maximum_condition: The largest condition number a regression may have.

    Raises:
        EconometricsInputError: If a value is not finite, a choice is
            malformed, or the regression would have fewer than 20 observations.
        IllConditionedError: If a regression's design is too ill-conditioned.
    """

    _require_deterministic(deterministic)
    _require_condition(maximum_condition)
    if not isinstance(lags, FixedLags | SelectedLags):
        raise EconometricsInputError(f"lags must be FixedLags or SelectedLags, got {lags!r}.")
    levels = require_series(values, 3, "An ADF series")
    differences = tuple(levels[t] - levels[t - 1] for t in range(1, len(levels)))
    widest = lags.count if isinstance(lags, FixedLags) else lags.maximum
    _require_dickey_fuller_sample(len(differences) - widest, "The ADF regression")
    shifted = (0.0, *levels[:-1])
    chosen = (
        lags.count
        if isinstance(lags, FixedLags)
        else _select(shifted, (0.0, *differences), deterministic, lags, maximum_condition)
    )
    fit = _dickey_fuller(
        shifted, (0.0, *differences), deterministic, chosen, chosen + 1, maximum_condition
    )
    level = len(_deterministic_names(deterministic))
    statistic = fit.coefficients[level] / fit.standard_errors[level]
    names = [
        *_deterministic_names(deterministic),
        "level_lag",
        *(f"difference_lag_{i}" for i in range(1, chosen + 1)),
    ]
    regression = Estimate(
        method=ADF_METHOD,
        data_id=series_identity(levels),
        parameters=dict(zip(names, fit.coefficients, strict=True)),
        standard_errors=dict(zip(names, fit.standard_errors, strict=True)),
        settings={
            "deterministic": deterministic.value,
            "lags": chosen,
            "maximum_condition": float(maximum_condition),
            "observations": fit.observations,
            **_lag_settings(lags),
        },
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
    )
    return UnitRootTest(
        null=Null.UNIT_ROOT,
        statistic=statistic,
        lags=chosen,
        observations=fit.observations,
        critical_values=mackinnon_critical_values(deterministic, 1, fit.observations),
        p_value=mackinnon_p_value(statistic, deterministic, 1),
        regression=regression,
    )


def _bandwidth(rule: Bandwidth, observations: int) -> int:
    if isinstance(rule, FixedBandwidth):
        return rule.lags
    if isinstance(rule, SchwertBandwidth):
        return rule.lags(observations)
    raise EconometricsInputError(
        f"bandwidth must be FixedBandwidth or SchwertBandwidth, got {rule!r}."
    )


def _bandwidth_settings(rule: Bandwidth) -> dict[str, Setting]:
    if isinstance(rule, FixedBandwidth):
        return {"bandwidth_rule": "fixed", "kernel": "bartlett"}
    return {"bandwidth_rule": "schwert", "bandwidth_constant": rule.constant, "kernel": "bartlett"}


def phillips_perron(
    values: Sequence[float],
    *,
    deterministic: Deterministic,
    bandwidth: Bandwidth,
    maximum_condition: float,
) -> UnitRootTest:
    """The Phillips-Perron test of a unit root in ``values``, by its ``Z_τ`` statistic.

    ``Z_τ = sqrt(gamma0 / λ²) t - (λ² - gamma0) T se(rho-hat) / (2 λ s)``: ``t`` the
    Dickey-Fuller t-statistic with no lags, ``gamma0`` the residuals' variance with
    divisor ``T``, ``λ²`` their long-run variance, ``s²`` the regression's
    residual variance. With ``λ² = gamma0`` it is the Dickey-Fuller statistic.

    Raises:
        EconometricsInputError: As :func:`augmented_dickey_fuller`, or if the
            residuals' long-run variance is zero.
        IllConditionedError: If the regression's design is too ill-conditioned.
    """

    _require_deterministic(deterministic)
    _require_condition(maximum_condition)
    levels = require_series(values, 3, "A Phillips-Perron series")
    differences = tuple(levels[t] - levels[t - 1] for t in range(1, len(levels)))
    _require_dickey_fuller_sample(len(differences), "The Phillips-Perron regression")
    lags = _bandwidth(bandwidth, len(differences))
    fit = _dickey_fuller(
        (0.0, *levels[:-1]), (0.0, *differences), deterministic, 0, 1, maximum_condition
    )
    level = len(_deterministic_names(deterministic))
    rho, error = fit.coefficients[level], fit.standard_errors[level]
    count = fit.observations
    gamma0 = fit.sum_of_squares / count
    lam2 = long_run_variance(fit.residuals, lags)
    if lam2 <= 0.0:
        raise EconometricsInputError(
            "The residuals' long-run variance is zero: the Phillips-Perron correction is undefined."
        )
    lam = math.sqrt(lam2)
    statistic = math.sqrt(gamma0 / lam2) * (rho / error) - (lam2 - gamma0) * (
        count * error / math.sqrt(fit.variance)
    ) / (2.0 * lam)
    names = [*_deterministic_names(deterministic), "level_lag"]
    regression = Estimate(
        method=PHILLIPS_PERRON_METHOD,
        data_id=series_identity(levels),
        parameters=dict(zip(names, fit.coefficients, strict=True)),
        standard_errors=dict(zip(names, fit.standard_errors, strict=True)),
        settings={
            "deterministic": deterministic.value,
            "bandwidth": lags,
            "maximum_condition": float(maximum_condition),
            "observations": count,
            **_bandwidth_settings(bandwidth),
        },
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
    )
    return UnitRootTest(
        null=Null.UNIT_ROOT,
        statistic=statistic,
        lags=lags,
        observations=count,
        critical_values=mackinnon_critical_values(deterministic, 1, count),
        p_value=mackinnon_p_value(statistic, deterministic, 1),
        regression=regression,
    )


def kpss(
    values: Sequence[float],
    *,
    deterministic: Deterministic,
    bandwidth: Bandwidth,
    maximum_condition: float,
) -> UnitRootTest:
    """The KPSS test of stationarity about a level or a trend.

    ``η = Σ S_t² / (T² λ²)``: ``S_t`` the partial sums of the residuals from
    regressing the series on its deterministic terms, ``λ²`` their long-run
    variance.

    Args:
        values: The series, in time order.
        deterministic: ``CONSTANT`` for level stationarity, ``CONSTANT_TREND``
            for trend stationarity.
        bandwidth: The long-run variance's lags.
        maximum_condition: The largest condition number the regression may have.

    Raises:
        EconometricsInputError: For another deterministic case, a malformed
            choice, a series of fewer than 20 values, or residuals whose
            long-run variance is zero.
        IllConditionedError: If the regression's design is too ill-conditioned.
    """

    _require_deterministic(deterministic)
    _require_condition(maximum_condition)
    kpss_critical_values(deterministic)  # refuses a case KPSS does not tabulate
    levels = require_series(values, 20, "A KPSS series")
    count = len(levels)
    lags = _bandwidth(bandwidth, count)
    fit = ordinary_least_squares(
        _deterministic_columns(deterministic, count), levels, maximum_condition
    )
    lam2 = long_run_variance(fit.residuals, lags)
    if lam2 <= 0.0:
        raise EconometricsInputError(
            "The residuals' long-run variance is zero: the KPSS statistic is undefined."
        )
    partial = 0.0
    squares = []
    for residual in fit.residuals:
        partial += residual
        squares.append(partial * partial)
    statistic = math.fsum(squares) / (count * count * lam2)
    names = _deterministic_names(deterministic)
    regression = Estimate(
        method=KPSS_METHOD,
        data_id=series_identity(levels),
        parameters=dict(zip(names, fit.coefficients, strict=True)),
        standard_errors=dict(zip(names, fit.standard_errors, strict=True)),
        settings={
            "deterministic": deterministic.value,
            "bandwidth": lags,
            "maximum_condition": float(maximum_condition),
            "observations": count,
            **_bandwidth_settings(bandwidth),
        },
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
    )
    return UnitRootTest(
        null=Null.STATIONARITY,
        statistic=statistic,
        lags=lags,
        observations=count,
        critical_values=kpss_critical_values(deterministic),
        p_value=kpss_p_value(statistic, deterministic),
        regression=regression,
    )
