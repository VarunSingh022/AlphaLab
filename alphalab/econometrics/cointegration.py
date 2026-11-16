"""Cointegration: Engle and Granger's test, and Johansen's (v4.3, ADR-0074, WS-07).

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

Johansen's tests
----------------
:func:`johansen` tests a system's cointegrating rank by likelihood ratios in its
VECM, under each of Osterwald-Lenum's five cases of deterministic terms.
``ΔX_t`` and ``X_{t-1}`` are each regressed on the lagged differences and the
unrestricted terms, and the eigenvalues are the squared canonical correlations
of the residuals. The trace statistic tests rank at most ``r`` against ``p``;
the maximum-eigenvalue statistic tests ``r`` against ``r + 1``. MacKinnon,
Haug and Michelis (1996) tabulate both, asymptotically, for ``p - r`` up to 12.
Every value is class 2: the eigenvalues are M2's symmetric eigensystem's, and
each logarithm is ``Decimal``'s, correctly rounded.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Final

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.estimate import Estimate, NumericalClass, Setting
from alphalab.common.linalg import (
    CholeskyFactor,
    IllConditionedError,
    NotPositiveDefiniteError,
    cholesky,
    least_squares,
    singular_values,
    symmetric_eigensystem,
)
from alphalab.econometrics._regression import ordinary_least_squares, residual_moments
from alphalab.econometrics.critical_values import (
    CriticalValues,
    Deterministic,
    JohansenCase,
    JohansenStatistic,
    PValue,
    johansen_critical_values,
    johansen_p_value,
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
    "JOHANSEN_METHOD",
    "MINIMUM_JOHANSEN_OBSERVATIONS",
    "VECM_METHOD",
    "CointegrationTest",
    "JohansenRank",
    "JohansenTest",
    "VecmFit",
    "engle_granger",
    "fit_vecm",
    "johansen",
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


#: Johansen's method scheme.
JOHANSEN_METHOD: Final = "alphalab.econometrics.cointegration.johansen/canonical-correlations.v1"

#: The most series MacKinnon, Haug and Michelis tabulate.
_MOST_SERIES: Final = 12

#: The fewest observations Johansen's test is run on: its tables are asymptotic.
MINIMUM_JOHANSEN_OBSERVATIONS: Final = 20


@dataclass(frozen=True, slots=True)
class JohansenRank:
    """The tests of cointegrating rank at most ``rank``.

    Attributes:
        rank: ``r``, the rank under the null.
        eigenvalue: ``λ_{r+1}``, the largest eigenvalue the null sets to zero.
        trace: ``-T Σ_{i>r} ln(1 - λ_i)``: rank at most ``r`` against ``p``.
        trace_critical_values: MacKinnon, Haug and Michelis's, for ``p - r``.
        trace_p_value: Interpolated in their table, a bound beyond it.
        maximum_eigenvalue: ``-T ln(1 - λ_{r+1})``: rank ``r`` against ``r + 1``.
        maximum_critical_values: As for the trace, for this statistic.
        maximum_p_value: As for the trace, for this statistic.
    """

    rank: int
    eigenvalue: float
    trace: float
    trace_critical_values: CriticalValues
    trace_p_value: PValue
    maximum_eigenvalue: float
    maximum_critical_values: CriticalValues
    maximum_p_value: PValue


@dataclass(frozen=True, slots=True)
class JohansenTest:
    """Johansen's tests of a system's cointegrating rank.

    Attributes:
        case: The VECM's deterministic terms.
        lags: The lagged differences in the VECM.
        observations: ``T``, the observations the moments are taken over.
        variables: ``p``, the series.
        ranks: The tests of rank at most ``r``, for ``r = 0`` to ``p - 1``.
        estimate: The eigenvalues and eigenvectors (class 2, with the
            eigensystem's convergence report). ``beta_j_*`` is the ``j``-th
            eigenvector, normalized so ``β' S11 β = I``: the first ``r`` are
            the cointegrating relations under rank ``r``.
    """

    case: JohansenCase
    lags: int
    observations: int
    variables: int
    ranks: tuple[JohansenRank, ...]
    estimate: Estimate


def johansen(
    series: Sequence[Sequence[float]],
    *,
    case: JohansenCase,
    lags: FixedLags,
    tolerance: float,
    max_iterations: int,
    maximum_condition: float,
) -> JohansenTest:
    """Johansen's trace and maximum-eigenvalue tests of a system's cointegrating rank.

    The VECM ``ΔX_t = Π X_{t-1} + Σ Γ_i ΔX_{t-i} + D_t + ε_t`` is reduced as
    Johansen reduces it. ``ΔX_t`` and ``X_{t-1}`` are each regressed on the
    lagged differences and the unrestricted deterministic terms; a restricted
    constant or trend joins ``X_{t-1}``. The eigenvalues are the squared
    canonical correlations of the two sets of residuals.

    Args:
        series: Two to twelve series of the same length, in time order.
        case: The VECM's deterministic terms, one of Osterwald-Lenum's five.
        lags: The VECM's lagged differences, a fixed count.
        tolerance: The eigensystem's criterion (M2's ``symmetric_eigensystem``).
        max_iterations: The eigensystem's iteration limit.
        maximum_condition: The largest condition number a regression may have.

    Raises:
        EconometricsInputError: If a value is not finite, the series differ in
            length, there are fewer than two or more than twelve, a choice is
            malformed, or fewer than 20 observations remain.
        IllConditionedError: If the regressors, or the levels' residuals, are
            too ill-conditioned: a series that is, or nearly is, a combination
            of the others.
        ConvergenceError: If the eigensystem does not meet its criterion.
    """

    reduction = _reduce(series, case, lags, tolerance, max_iterations, maximum_condition)
    count, observations = reduction.count, reduction.observations
    eigenvalues, vectors, names = reduction.eigenvalues, reduction.vectors, reduction.names
    p = reduction.lags
    logs = [_log_complement(value) for value in eigenvalues]
    ranks = []
    for rank in range(count):
        dimension = count - rank
        trace = _statistic(observations, logs[rank:])
        maximum = _statistic(observations, logs[rank : rank + 1])
        ranks.append(
            JohansenRank(
                rank=rank,
                eigenvalue=eigenvalues[rank],
                trace=trace,
                trace_critical_values=johansen_critical_values(
                    case, JohansenStatistic.TRACE, dimension
                ),
                trace_p_value=johansen_p_value(trace, case, JohansenStatistic.TRACE, dimension),
                maximum_eigenvalue=maximum,
                maximum_critical_values=johansen_critical_values(
                    case, JohansenStatistic.MAXIMUM_EIGENVALUE, dimension
                ),
                maximum_p_value=johansen_p_value(
                    maximum, case, JohansenStatistic.MAXIMUM_EIGENVALUE, dimension
                ),
            )
        )

    parameters = {f"eigenvalue_{j + 1}": value for j, value in enumerate(eigenvalues)}
    for j, vector in enumerate(vectors):
        for name, value in zip(names, vector, strict=True):
            parameters[f"beta_{j + 1}_{name}"] = value
    estimate = Estimate(
        method=JOHANSEN_METHOD,
        data_id=system_identity(reduction.xs),
        parameters=parameters,
        standard_errors={},
        settings={
            "case": case.value,
            "lags": p,
            "maximum_condition": float(maximum_condition),
            "observations": observations,
            "tolerance": float(tolerance),
            "max_iterations": max_iterations,
            "variables": count,
        },
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
        convergence=reduction.convergence,
    )
    return JohansenTest(
        case=case,
        lags=p,
        observations=observations,
        variables=count,
        ranks=tuple(ranks),
        estimate=estimate,
    )


@dataclass(frozen=True, slots=True)
class _Reduction:
    """Johansen's reduction of a system: what both the test and the VECM read."""

    xs: list[tuple[float, ...]]
    count: int
    lags: int
    observations: int
    #: The columns of ``X_{t-1}``, a restricted term's last: ``series_k``, ``constant``, ``trend``.
    names: list[str]
    stacked: list[list[float]]
    #: The lagged differences, then the unrestricted terms, each a column of ``T`` rows.
    regressors: list[list[float]]
    regressor_names: list[str]
    differences: list[list[float]]
    eigenvalues: tuple[float, ...]
    vectors: list[list[float]]
    convergence: ConvergenceReport


def _reduce(
    series: Sequence[Sequence[float]],
    case: JohansenCase,
    lags: FixedLags,
    tolerance: float,
    max_iterations: int,
    maximum_condition: float,
) -> _Reduction:
    """Johansen's reduction: the refusals, the residual moments and the eigensystem."""

    if not isinstance(case, JohansenCase):
        raise EconometricsInputError(f"case must be a JohansenCase, got {case!r}.")
    if not isinstance(lags, FixedLags):
        raise EconometricsInputError(
            f"A VECM's lags are a FixedLags count, got {lags!r}: choosing them is "
            "the order selection of a VAR, stated separately."
        )
    _require_condition(maximum_condition)
    count = len(series)
    if not 2 <= count <= _MOST_SERIES:
        raise EconometricsInputError(
            f"Johansen's test takes 2 to {_MOST_SERIES} series, the systems MacKinnon, Haug "
            f"and Michelis tabulate, got {count}."
        )
    xs = [require_series(each, 3, f"Series {i + 1}") for i, each in enumerate(series)]
    length = len(xs[0])
    for i, each in enumerate(xs):
        if len(each) != length:
            raise EconometricsInputError(
                f"Series {i + 1} has {len(each)} values and series 1 {length}: a system "
                "is observed over one set of instants."
            )
    p = lags.count
    start = p + 1
    observations = length - start
    if observations < MINIMUM_JOHANSEN_OBSERVATIONS:
        raise EconometricsInputError(
            f"With {p} lags, {length} values leave {observations} observations; Johansen's "
            f"test takes {MINIMUM_JOHANSEN_OBSERVATIONS} at least: its tables are asymptotic."
        )

    rows = range(start, length)
    differences = [[x[t] - x[t - 1] for t in rows] for x in xs]
    levels = [[x[t - 1] for t in rows] for x in xs]
    regressors = [[x[t - i] - x[t - i - 1] for t in rows] for i in range(1, p + 1) for x in xs]
    regressor_names = [f"gamma_{i}_series_{k + 1}" for i in range(1, p + 1) for k in range(count)]
    ones = [1.0] * observations
    trend = [(position + 1) / observations for position in range(observations)]
    extra: list[list[float]] = []
    names = [f"series_{i + 1}" for i in range(count)]
    if case is JohansenCase.RESTRICTED_CONSTANT:
        extra, names = [ones], [*names, "constant"]
    elif case is JohansenCase.UNRESTRICTED_CONSTANT:
        regressors.append(ones)
        regressor_names.append("constant")
    elif case is JohansenCase.RESTRICTED_TREND:
        regressors.append(ones)
        regressor_names.append("constant")
        extra, names = [trend], [*names, "trend"]
    elif case is JohansenCase.UNRESTRICTED_TREND:
        regressors += [ones, trend]
        regressor_names += ["constant", "trend"]
    stacked = [*levels, *extra]
    width = len(stacked)
    moments = residual_moments(regressors, [*stacked, *differences], maximum_condition)
    s11 = [list(row[:width]) for row in moments[:width]]
    s10 = [list(row[width:]) for row in moments[:width]]
    s00 = [list(row[width:]) for row in moments[width:]]
    level_factor = _factor(s11, maximum_condition, "The levels' residuals")
    difference_factor = _factor(s00, maximum_condition, "The differences' residuals")

    # C = L1⁻¹ S10 L0⁻ᵀ; its rows' Gram matrix C C' has the squared canonical correlations.
    whitened = _transpose([_forward(level_factor, column) for column in _transpose(s10)])
    canonical = [_forward(difference_factor, row) for row in whitened]
    gram = [
        [math.fsum(map(operator.mul, canonical[i], canonical[k])) for k in range(width)]
        for i in range(width)
    ]
    system = symmetric_eigensystem(gram, tolerance=tolerance, max_iterations=max_iterations)
    eigenvalues = system.eigenvalues[:count]
    if not eigenvalues[0] < 1.0:
        raise EconometricsInputError(
            f"The largest eigenvalue is {eigenvalues[0]!r}: a canonical correlation of one, a "
            "relation the residuals satisfy exactly, which leaves no likelihood ratio to test."
        )
    vectors = [_backward(level_factor, vector) for vector in system.eigenvectors[:count]]

    return _Reduction(
        xs=xs,
        count=count,
        lags=p,
        observations=observations,
        names=names,
        stacked=stacked,
        regressors=regressors,
        regressor_names=regressor_names,
        differences=differences,
        eigenvalues=eigenvalues,
        vectors=vectors,
        convergence=system.convergence,
    )


def _factor(matrix: list[list[float]], maximum_condition: float, what: str) -> CholeskyFactor:
    """The Cholesky factor of a residual moment matrix, refused when ill-conditioned."""

    try:
        factor = cholesky(matrix)
    except NotPositiveDefiniteError as error:
        raise IllConditionedError(
            f"{what} are rank deficient: a series is a combination of the others.",
            math.inf,
            maximum_condition,
        ) from error
    values = singular_values(factor.lower)
    condition = math.inf if values[-1] == 0.0 else values[0] / values[-1]
    if not condition <= maximum_condition:
        raise IllConditionedError(
            f"{what} are conditioned at {condition:.6g}, beyond the stated maximum of "
            f"{maximum_condition:g}: a series nearly a combination of the others.",
            condition,
            maximum_condition,
        )
    return factor


def _forward(factor: CholeskyFactor, values: Sequence[float]) -> list[float]:
    """``L⁻¹ v`` by forward substitution, every inner product a ``fsum``."""

    lower = factor.lower
    solution: list[float] = []
    for row, value in enumerate(values):
        known = math.fsum(map(operator.mul, lower[row][:row], solution))
        solution.append((value - known) / lower[row][row])
    return solution


def _backward(factor: CholeskyFactor, values: Sequence[float]) -> list[float]:
    """``L'⁻¹ v`` by back substitution, every inner product a ``fsum``."""

    lower = factor.lower
    size = len(lower)
    solution = [0.0] * size
    for row in range(size - 1, -1, -1):
        known = math.fsum(lower[k][row] * solution[k] for k in range(row + 1, size))
        solution[row] = (values[row] - known) / lower[row][row]
    return solution


def _transpose(rows: Sequence[Sequence[float]]) -> list[list[float]]:
    return [list(column) for column in zip(*rows, strict=True)]


def _log_complement(eigenvalue: float) -> Decimal:
    """``ln(1 - λ)``, correctly rounded: an eigenvalue rounding left below zero is zero."""

    with localcontext() as context:
        context.prec = 34
        return (Decimal(1) - Decimal(max(eigenvalue, 0.0))).ln()


def _statistic(observations: int, logs: Sequence[Decimal]) -> float:
    """``-T Σ ln(1 - λ_i)``, summed and scaled in ``Decimal``, rounded once to a float."""

    with localcontext() as context:
        context.prec = 34
        return float(-observations * sum(logs, Decimal(0)))


#: The VECM's method scheme: Johansen's reduction, then least squares given its relations.
VECM_METHOD: Final = "alphalab.econometrics.cointegration.fit_vecm/johansen-two-step.v1"


@dataclass(frozen=True, slots=True)
class VecmFit:
    """A VECM of a stated cointegrating rank, fitted by Johansen's two steps.

    Attributes:
        case: The deterministic terms.
        rank: ``r``, the cointegrating relations.
        lags: The lagged differences.
        observations: ``T``.
        relations: ``β``, the relations, normalized so that the first ``r``
            series' block is the identity: ``beta_j_<column>`` for relation
            ``j`` (no standard errors: ``β`` is super-consistent, and the second
            step takes it as given).
        equations: One estimate per series' equation: ``alpha_j`` on relation
            ``j``'s error-correction term, ``gamma_<lag>_<series>`` on each
            lagged difference, and the unrestricted constant or trend, each
            with its least-squares standard error.
        covariance: The residuals' covariance, ``sigma_<i>_<k>``, over ``T``.
    """

    case: JohansenCase
    rank: int
    lags: int
    observations: int
    relations: Estimate
    equations: tuple[Estimate, ...]
    covariance: Estimate


def fit_vecm(
    series: Sequence[Sequence[float]],
    *,
    rank: int,
    case: JohansenCase,
    lags: FixedLags,
    tolerance: float,
    max_iterations: int,
    maximum_condition: float,
) -> VecmFit:
    """Fit a VECM of cointegrating rank ``rank``: Johansen's relations, then least squares.

    Step one is :func:`johansen`'s reduction: its first ``rank`` eigenvectors
    are the relations, normalized so that the first ``rank`` series' block is
    the identity. Step two regresses each series' difference on the relations'
    error-correction terms, the lagged differences and the unrestricted
    deterministic terms, by least squares (class 2), with ``β`` taken as given.

    Args:
        series: Two to twelve series of the same length, in time order.
        rank: The cointegrating relations, 1 to one less than the series: rank
            0 is a VAR in differences, and full rank a stationary VAR.
        case, lags, tolerance, max_iterations, maximum_condition: As for
            :func:`johansen`.

    Raises:
        EconometricsInputError: For a rank outside ``1 .. p - 1``, or a
            relation block that cannot be normalized, besides what
            :func:`johansen` refuses.
        IllConditionedError: As :func:`johansen`, and for a second-step design
            too ill-conditioned to solve.
        ConvergenceError: If the eigensystem does not meet its criterion.
    """

    reduction = _reduce(series, case, lags, tolerance, max_iterations, maximum_condition)
    count = reduction.count
    if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= count - 1:
        raise EconometricsInputError(
            f"A VECM's rank is 1 to {count - 1} for {count} series, got {rank!r}: rank 0 is a "
            "VAR in differences, and full rank a stationary VAR."
        )
    vectors = [list(vector) for vector in reduction.vectors[:rank]]  # rank vectors, each of width
    width = len(reduction.names)
    # The block of the first `rank` series: block[i][j] = vector_j[i].
    block = [[vectors[j][i] for j in range(rank)] for i in range(rank)]
    inverse = _inverse(block, maximum_condition)
    # beta_norm[c][j] = sum_k vector_k[c] * inverse[k][j]. The first rank rows are the
    # identity by definition: they are set so, not left as rounding leaves them.
    normalized = [
        [
            (1.0 if c == j else 0.0)
            if c < rank
            else math.fsum(vectors[k][c] * inverse[k][j] for k in range(rank))
            for j in range(rank)
        ]
        for c in range(width)
    ]
    terms = [
        [
            math.fsum(normalized[c][j] * reduction.stacked[c][t] for c in range(width))
            for t in range(reduction.observations)
        ]
        for j in range(rank)
    ]
    design_columns = [*terms, *reduction.regressors]
    design = [list(row) for row in zip(*design_columns, strict=True)]
    names = [
        *(f"alpha_{j + 1}" for j in range(rank)),
        *reduction.regressor_names,
    ]
    data_id = system_identity(reduction.xs)
    settings: dict[str, Setting] = {
        "case": case.value,
        "lags": reduction.lags,
        "max_iterations": max_iterations,
        "maximum_condition": float(maximum_condition),
        "observations": reduction.observations,
        "rank": rank,
        "tolerance": float(tolerance),
    }
    equations = []
    residuals = []
    for i, target in enumerate(reduction.differences):
        fit = ordinary_least_squares(design, target, maximum_condition)
        residuals.append(fit.residuals)
        equations.append(
            Estimate(
                method=VECM_METHOD,
                data_id=data_id,
                parameters=dict(zip(names, fit.coefficients, strict=True)),
                standard_errors=dict(zip(names, fit.standard_errors, strict=True)),
                settings={**settings, "equation": i + 1},
                numerical_class=NumericalClass.CORRECTLY_ROUNDED,
            )
        )
    relations = Estimate(
        method=VECM_METHOD,
        data_id=data_id,
        parameters={
            f"beta_{j + 1}_{name}": normalized[c][j]
            for j in range(rank)
            for c, name in enumerate(reduction.names)
        },
        standard_errors={},
        settings=settings,
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
        convergence=reduction.convergence,
    )
    covariance = Estimate(
        method=VECM_METHOD,
        data_id=data_id,
        parameters={
            f"sigma_{i + 1}_{k + 1}": math.fsum(map(operator.mul, residuals[i], residuals[k]))
            / reduction.observations
            for i in range(count)
            for k in range(count)
        },
        standard_errors={},
        settings=settings,
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
    )
    return VecmFit(
        case=case,
        rank=rank,
        lags=reduction.lags,
        observations=reduction.observations,
        relations=relations,
        equations=tuple(equations),
        covariance=covariance,
    )


def _inverse(block: list[list[float]], maximum_condition: float) -> list[list[float]]:
    """``block⁻¹`` by M2's least squares, column by column, refused when ill-conditioned."""

    size = len(block)
    try:
        columns = [
            least_squares(block, [1.0 if i == j else 0.0 for i in range(size)], maximum_condition)
            for j in range(size)
        ]
    except IllConditionedError as error:
        raise EconometricsInputError(
            "The relations' block for the first series is singular: those series do not "
            "identify the relations, so they cannot be normalized on them. Order the series "
            "so that the first ones enter the relations."
        ) from error
    return [[columns[j].coefficients[i] for j in range(size)] for i in range(size)]
