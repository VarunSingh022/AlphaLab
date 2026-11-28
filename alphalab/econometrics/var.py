"""Vector autoregressions (v4.3, ADR-0074, WS-07).

A VAR(p) regresses each of ``n`` series on its deterministic terms and ``p`` lags
of every series, by least squares, one equation at a time (class 2). Each
equation's estimate carries its coefficients with their classical standard
errors; the residual covariance is over ``T``, the likelihood's.

Lags
----
A fixed count, or chosen by AIC or BIC. Every candidate from 1 to the maximum
is fitted on the sample the maximum leaves, so their criteria compare; the
chosen count is then fitted on all the data it leaves, as the unit-root tests'
selection does. A candidate's criterion is ``T ln det Σ + penalty``, ``penalty``
``2k`` for AIC and ``k ln T`` for BIC, with ``k`` the system's coefficients.
``ln det Σ`` is the sum of the logarithms of its Cholesky pivots, each
``Decimal``'s, correctly rounded: the same choice on every host.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Final

from alphalab.common.estimate import Estimate, NumericalClass, Setting
from alphalab.common.linalg import NotPositiveDefiniteError, cholesky
from alphalab.econometrics._regression import ordinary_least_squares
from alphalab.econometrics.critical_values import Deterministic
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import require_series, system_identity
from alphalab.econometrics.unit_root import (
    FixedLags,
    LagCriterion,
    LagRule,
    SelectedLags,
    _deterministic_columns,
    _deterministic_names,
    _lag_settings,
    _require_condition,
    _require_deterministic,
)

__all__ = ["MINIMUM_VAR_OBSERVATIONS", "VAR_METHOD", "VarFit", "fit_var"]

#: The method scheme of a VAR's equations and covariance.
VAR_METHOD: Final = "alphalab.econometrics.var.fit_var/ols.v1"
#: The fewest observations a VAR is fitted on.
MINIMUM_VAR_OBSERVATIONS: Final = 20


@dataclass(frozen=True, slots=True)
class VarFit:
    """A VAR(p) fitted equation by equation.

    Attributes:
        deterministic: The deterministic terms in every equation.
        lags: ``p``, fixed or chosen.
        observations: ``T``, the rows each equation is fitted on.
        equations: One estimate per series: its deterministic terms, then
            ``phi_<lag>_series_<k>`` for each lag of each series, each with its
            standard error.
        covariance: ``sigma_<i>_<k>``, the residuals' cross-products over ``T``.
    """

    deterministic: Deterministic
    lags: int
    observations: int
    equations: tuple[Estimate, ...]
    covariance: Estimate


def fit_var(
    series: Sequence[Sequence[float]],
    *,
    deterministic: Deterministic,
    lags: LagRule,
    maximum_condition: float,
) -> VarFit:
    """Fit a VAR to ``series``, every choice stated.

    Args:
        series: One or more series of the same length, in time order.
        deterministic: The terms in every equation.
        lags: A fixed count of at least one, or AIC or BIC choosing among 1 to
            a maximum.
        maximum_condition: The largest condition number a regression may have.

    Raises:
        EconometricsInputError: If a value is not finite, the series differ in
            length, a choice is malformed (no lag, or the t-statistic rule,
            which chooses one equation's lags), or fewer than 20 observations
            remain.
        IllConditionedError: If a design is too ill-conditioned: a series that
            is, or nearly is, a combination of the others.
    """

    _require_deterministic(deterministic)
    _require_condition(maximum_condition)
    if not isinstance(lags, FixedLags | SelectedLags):
        raise EconometricsInputError(f"lags must be FixedLags or SelectedLags, got {lags!r}.")
    if isinstance(lags, SelectedLags) and lags.criterion is LagCriterion.T_STATISTIC:
        raise EconometricsInputError(
            "A VAR's lags are chosen for the system, by AIC or BIC: the t-statistic rule "
            "chooses one equation's."
        )
    widest = lags.count if isinstance(lags, FixedLags) else lags.maximum
    if widest < 1:
        raise EconometricsInputError(
            f"A VAR has one lag at least, got {widest}: without lags it is its deterministic "
            "terms alone."
        )
    if not series:
        raise EconometricsInputError("A VAR is of one series at least, got none.")
    xs = [require_series(each, 3, f"Series {i + 1}") for i, each in enumerate(series)]
    length = len(xs[0])
    for i, each in enumerate(xs):
        if len(each) != length:
            raise EconometricsInputError(
                f"Series {i + 1} has {len(each)} values and series 1 {length}: a system "
                "is observed over one set of instants."
            )
    if length - widest < MINIMUM_VAR_OBSERVATIONS:
        raise EconometricsInputError(
            f"With {widest} lags, {length} values leave {length - widest} observations; a "
            f"VAR is fitted on {MINIMUM_VAR_OBSERVATIONS} at least."
        )

    chosen = (
        lags.count
        if isinstance(lags, FixedLags)
        else _select(xs, deterministic, lags, maximum_condition)
    )
    design, observations = _design(xs, deterministic, chosen, chosen)
    names = [
        *_deterministic_names(deterministic),
        *(f"phi_{i}_series_{k + 1}" for i in range(1, chosen + 1) for k in range(len(xs))),
    ]
    data_id = system_identity(xs)
    settings: dict[str, Setting] = {
        "deterministic": deterministic.value,
        "lags": chosen,
        "maximum_condition": float(maximum_condition),
        "observations": observations,
        **_lag_settings(lags),
    }
    equations = []
    residuals = []
    for i, x in enumerate(xs):
        fit = ordinary_least_squares(design, x[chosen:], maximum_condition)
        residuals.append(fit.residuals)
        equations.append(
            Estimate(
                method=VAR_METHOD,
                data_id=data_id,
                parameters=dict(zip(names, fit.coefficients, strict=True)),
                standard_errors=dict(zip(names, fit.standard_errors, strict=True)),
                settings={**settings, "equation": i + 1},
                numerical_class=NumericalClass.CORRECTLY_ROUNDED,
            )
        )
    count = len(xs)
    covariance = Estimate(
        method=VAR_METHOD,
        data_id=data_id,
        parameters={
            f"sigma_{i + 1}_{k + 1}": math.fsum(map(operator.mul, residuals[i], residuals[k]))
            / observations
            for i in range(count)
            for k in range(count)
        },
        standard_errors={},
        settings=settings,
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
    )
    return VarFit(deterministic, chosen, observations, tuple(equations), covariance)


def _design(
    xs: list[tuple[float, ...]], deterministic: Deterministic, lags: int, start: int
) -> tuple[list[list[float]], int]:
    """The rows from ``start``: the deterministic terms, then each lag of each series."""

    observations = len(xs[0]) - start
    terms = _deterministic_columns(deterministic, observations)
    rows = [
        [*terms[t - start], *(x[t - i] for i in range(1, lags + 1) for x in xs)]
        for t in range(start, len(xs[0]))
    ]
    return rows, observations


def _select(
    xs: list[tuple[float, ...]],
    deterministic: Deterministic,
    rule: SelectedLags,
    maximum_condition: float,
) -> int:
    """The lag count ``rule`` chooses, every candidate fitted on the maximum's sample."""

    best: tuple[Decimal, int] | None = None
    count = len(xs)
    for candidate in range(1, rule.maximum + 1):
        design, observations = _design(xs, deterministic, candidate, rule.maximum)
        residuals = [
            ordinary_least_squares(design, x[rule.maximum :], maximum_condition).residuals
            for x in xs
        ]
        sigma = [
            [
                math.fsum(map(operator.mul, residuals[i], residuals[k])) / observations
                for k in range(count)
            ]
            for i in range(count)
        ]
        try:
            pivots = cholesky(sigma).pivots
        except NotPositiveDefiniteError as error:
            raise EconometricsInputError(
                f"With {candidate} lags the system fits exactly: its residual covariance is "
                "singular and its criterion undefined."
            ) from error
        with localcontext() as context:
            context.prec = 34
            log_determinant = sum((Decimal(pivot).ln() for pivot in pivots), Decimal(0))
            size = Decimal(observations)
            coefficients = Decimal(len(design[0]) * count)
            penalty = (
                2 * coefficients if rule.criterion is LagCriterion.AIC else coefficients * size.ln()
            )
            value = size * log_determinant + penalty
        if best is None or value < best[0]:
            best = (value, candidate)
    assert best is not None, "the maximum is at least one, so one candidate was fitted"
    return best[1]
