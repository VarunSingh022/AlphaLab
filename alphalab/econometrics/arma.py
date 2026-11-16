"""ARMA models: conditional least squares and the exact Gaussian likelihood (v4.3, ADR-0074, WS-07).

The model is ``y_t - mu = sum_i phi_i (y_{t-i} - mu) + e_t + sum_j theta_j e_{t-j}``
with ``e_t ~ N(0, sigma²)``: ``p`` autoregressive and ``q`` moving-average
coefficients, about a mean ``mu`` when the deterministic term is a constant
and about zero when it is none.

Two estimators
--------------
* **Conditional least squares** (:attr:`ArmaMethod.CONDITIONAL_LEAST_SQUARES`).
  The first ``p`` values are given and the errors before them are zero; the
  residuals follow by the recursion, and their sum of squares is minimised.
  That is the conditional Gaussian likelihood's maximum, its variance the mean
  squared residual. Every step is ``+ - * /``, square roots and ``fsum``, and
  BFGS's are too: class 2, the same bits on every host.
* **The exact Gaussian likelihood** (:attr:`ArmaMethod.EXACT_LIKELIHOOD`).
  Harvey's state-space form, filtered from its stationary covariance, scores
  every value, and the variance is concentrated out. ``log``: class 3.

Admissible by construction
--------------------------
BFGS moves in partial autocorrelations, each ``u / sqrt(1 + u²)``, so each
lies in ``(-1, 1)``, and Durbin and Levinson's recursion turns them into
coefficients: the autoregressive polynomial is stationary and the
moving-average polynomial invertible at every point the optimiser can reach
(Barndorff-Nielsen and Schou, 1973). The map is ``+ - * /`` and a square root,
so it keeps conditional least squares in class 2. An explicit start outside
the region is refused, by the recursion run backward.

Standard errors
---------------
Conditional least squares takes Gauss and Newton's ``sigma² (J'J)⁻¹``, ``J`` the
residuals' derivatives by central differences: class 2. The exact likelihood
takes the curvature of the concentrated likelihood in the optimiser's
coordinates, mapped to the coefficients by the delta method through the
recursion's Jacobian: class 3. The variance's is ``sigma² sqrt(2 / m)``, ``m``
the observations scored. A common factor in the two polynomials leaves the
coefficients unidentified: a design or curvature that is not positive definite
is refused.

Orders by an information criterion
----------------------------------
:func:`select_arma` fits every order up to a maximum and keeps the least
criterion's, ``-2 ln L + 2k`` (AIC) or ``-2 ln L + k ln m`` (BIC), ``k`` the
parameters with the variance. Under conditional least squares every
candidate is conditioned on the first ``P`` values, ``P`` the largest
autoregressive order, so each scores the same residuals, and the criterion
``m ln(SSR / m) + penalty`` is taken by ``Decimal`` logarithms: the same choice
on every host. The chosen order is refitted on all the data it leaves, as the
unit-root tests' lags are. Under the exact likelihood every candidate scores
every value, and the criterion is class 3.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from typing import Final

from alphalab.common.estimate import Estimate, NumericalClass, Setting
from alphalab.common.linalg import NotPositiveDefiniteError, cholesky
from alphalab.common.optimize import bfgs, numerical_gradient
from alphalab.econometrics._likelihood import curvature_covariance
from alphalab.econometrics.critical_values import Deterministic
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import require_series, series_identity
from alphalab.econometrics.unit_root import LagCriterion

__all__ = [
    "MINIMUM_ARMA_OBSERVATIONS",
    "ArmaFit",
    "ArmaMethod",
    "ArmaSelection",
    "ArmaStart",
    "fit_arma",
    "select_arma",
]

#: The observations a fit scores at least: after the ``p`` it conditions on, by least squares.
MINIMUM_ARMA_OBSERVATIONS: Final = 50

_LOG_TWO_PI: Final = math.log(2.0 * math.pi)


class ArmaMethod(Enum):
    """How an ARMA model is fitted."""

    #: The conditional sum of squares: class 2.
    CONDITIONAL_LEAST_SQUARES = "conditional-least-squares"
    #: The exact Gaussian likelihood, by the Kalman filter: class 3.
    EXACT_LIKELIHOOD = "exact-likelihood"

    @property
    def scheme(self) -> str:
        """The method scheme an estimate records."""

        return f"alphalab.econometrics.arma.fit_arma/{self.value}.v1"


class ArmaStart(Enum):
    """A named start rule."""

    #: Every coefficient zero, and the mean the sample's.
    ZEROS = "zeros"


@dataclass(frozen=True, slots=True)
class ArmaFit:
    """An ARMA model fitted.

    Attributes:
        order: ``(p, q)``.
        method: How it was fitted.
        estimate: The mean (with a constant), ``ar_1`` to ``ar_p``, ``ma_1`` to
            ``ma_q`` and the ``variance``, with standard errors.
        log_likelihood: The conditional likelihood (least squares) or the
            exact one, at the estimate.
        residuals: The residuals scored: least squares', or the exact
            likelihood's one-step prediction errors.
        observations: ``m``, the values scored.
        aic: ``-2 ln L + 2k``.
        bic: ``-2 ln L + k ln m``.
    """

    order: tuple[int, int]
    method: ArmaMethod
    estimate: Estimate
    log_likelihood: float
    residuals: tuple[float, ...]
    observations: int
    aic: float
    bic: float


@dataclass(frozen=True, slots=True)
class ArmaSelection:
    """The order an information criterion chose, and every candidate's criterion.

    Attributes:
        criterion: AIC or BIC.
        chosen: The chosen order's fit, on all the data it leaves.
        candidates: Every order fitted, with its criterion, in the grid's order.
    """

    criterion: LagCriterion
    chosen: ArmaFit
    candidates: tuple[tuple[tuple[int, int], float], ...]


# --- the region, and the coordinates that keep to it --------------------------------


def _from_partial(partial: Sequence[float]) -> list[float]:
    """Durbin and Levinson's recursion: partial autocorrelations to AR coefficients."""

    coefficients: list[float] = []
    for k, r in enumerate(partial, start=1):
        coefficients = [coefficients[j] - r * coefficients[k - 2 - j] for j in range(k - 1)] + [r]
    return coefficients


def _to_partial(coefficients: Sequence[float]) -> list[float] | None:
    """The recursion run backward; ``None`` outside the stationary region."""

    current = list(coefficients)
    partial = []
    for k in range(len(current), 0, -1):
        r = current[k - 1]
        if not -1.0 < r < 1.0:
            return None
        denominator = 1.0 - r * r
        current = [(current[j] + r * current[k - 2 - j]) / denominator for j in range(k - 1)]
        partial.append(r)
    partial.reverse()
    return partial


def _bounded(u: float) -> float:
    return u / math.sqrt(1.0 + u * u)


def _unbounded(r: float) -> float:
    return r / math.sqrt(1.0 - r * r)


@dataclass(frozen=True, slots=True)
class _Shape:
    p: int
    q: int
    constant: bool

    @property
    def names(self) -> list[str]:
        return [
            *(("mean",) if self.constant else ()),
            *(f"ar_{i}" for i in range(1, self.p + 1)),
            *(f"ma_{j}" for j in range(1, self.q + 1)),
        ]

    def natural(self, point: Sequence[float]) -> tuple[float, list[float], list[float]]:
        """The mean, the AR and the MA coefficients at the optimiser's ``point``."""

        offset = 1 if self.constant else 0
        mean = point[0] if self.constant else 0.0
        ar = _from_partial([_bounded(u) for u in point[offset : offset + self.p]])
        ma = [-a for a in _from_partial([_bounded(u) for u in point[offset + self.p :]])]
        return mean, ar, ma

    def point(self, mean: float, ar: Sequence[float], ma: Sequence[float]) -> list[float] | None:
        """The optimiser's coordinates of a model, or ``None`` outside the region."""

        ar_partial = _to_partial(ar)
        ma_partial = _to_partial([-theta for theta in ma])
        if ar_partial is None or ma_partial is None:
            return None
        return [
            *((mean,) if self.constant else ()),
            *(_unbounded(r) for r in ar_partial),
            *(_unbounded(r) for r in ma_partial),
        ]

    def flat(self, mean: float, ar: Sequence[float], ma: Sequence[float]) -> list[float]:
        return [*((mean,) if self.constant else ()), *ar, *ma]

    def unflat(self, values: Sequence[float]) -> tuple[float, list[float], list[float]]:
        offset = 1 if self.constant else 0
        mean = values[0] if self.constant else 0.0
        return (
            mean,
            list(values[offset : offset + self.p]),
            list(values[offset + self.p : offset + self.p + self.q]),
        )


# --- the two objectives ---------------------------------------------------------------


def _residuals(
    values: Sequence[float],
    mean: float,
    ar: Sequence[float],
    ma: Sequence[float],
    conditioned: int,
) -> list[float]:
    """The residuals from ``conditioned`` on, the errors before them zero."""

    centered = [value - mean for value in values]
    errors = [0.0] * len(centered)
    for t in range(conditioned, len(centered)):
        errors[t] = math.fsum(
            [
                centered[t],
                *(-phi * centered[t - 1 - i] for i, phi in enumerate(ar)),
                *(-theta * errors[t - 1 - j] for j, theta in enumerate(ma) if t - 1 - j >= 0),
            ]
        )
    return errors[conditioned:]


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting; refused on an exactly zero pivot."""

    size = len(rhs)
    rows = [[*row, value] for row, value in zip(matrix, rhs, strict=True)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if rows[pivot][column] == 0.0:
            raise EconometricsInputError(
                "The stationary covariance does not exist: a partial autocorrelation reached "
                "the region's edge in rounding."
            )
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for r in range(column + 1, size):
            ratio = rows[r][column] / rows[column][column]
            if ratio != 0.0:
                rows[r] = [a - ratio * b for a, b in zip(rows[r], rows[column], strict=True)]
    solution = [0.0] * size
    for r in range(size - 1, -1, -1):
        known = math.fsum([rows[r][k] * solution[k] for k in range(r + 1, size)])
        solution[r] = (rows[r][size] - known) / rows[r][r]
    return solution


def _exact(
    values: Sequence[float], mean: float, ar: Sequence[float], ma: Sequence[float]
) -> tuple[float, list[float], float]:
    """The exact Gaussian log-likelihood with the variance concentrated out.

    Harvey's form with ``sigma² = 1``: ``a_{t+1} = T a_t + R e_{t+1}``, ``y_t - mu = Z a_t``,
    from the stationary covariance ``P = T P T' + R R'``. Returns the
    log-likelihood, the one-step prediction errors and the variance.
    """

    size = max(len(ar), len(ma) + 1)
    transition = [[0.0] * size for _ in range(size)]
    for i, phi in enumerate(ar):
        transition[i][0] = phi
    for i in range(size - 1):
        transition[i][i + 1] = 1.0
    loading = [1.0, *(ma[j] if j < len(ma) else 0.0 for j in range(size - 1))]
    shock = [[loading[i] * loading[k] for k in range(size)] for i in range(size)]
    # (I - T (x) T) vec(P) = vec(R R'), row i * size + k.
    lyapunov = [
        [
            (1.0 if (i, k) == (j, m) else 0.0) - transition[i][j] * transition[k][m]
            for j in range(size)
            for m in range(size)
        ]
        for i in range(size)
        for k in range(size)
    ]
    vector = _solve(lyapunov, [shock[i][k] for i in range(size) for k in range(size)])
    covariance = [
        [(vector[i * size + k] + vector[k * size + i]) / 2.0 for k in range(size)]
        for i in range(size)
    ]
    state = [0.0] * size
    errors, scaled, logs = [], [], []
    for value in values:
        variance = covariance[0][0]
        if not variance > 0.0:
            raise EconometricsInputError("The one-step variance is not positive.")
        error = (value - mean) - state[0]
        column = [covariance[i][0] for i in range(size)]
        filtered = [state[i] + column[i] * error / variance for i in range(size)]
        reduced = [
            [covariance[i][k] - column[i] * column[k] / variance for k in range(size)]
            for i in range(size)
        ]
        state = [
            math.fsum([transition[i][k] * filtered[k] for k in range(size)]) for i in range(size)
        ]
        moved = [
            [
                math.fsum([transition[i][k] * reduced[k][m] for k in range(size)])
                for m in range(size)
            ]
            for i in range(size)
        ]
        covariance = [
            [
                math.fsum([moved[i][m] * transition[k][m] for m in range(size)]) + shock[i][k]
                for k in range(size)
            ]
            for i in range(size)
        ]
        covariance = [
            [(covariance[i][k] + covariance[k][i]) / 2.0 for k in range(size)] for i in range(size)
        ]
        errors.append(error)
        scaled.append(error * error / variance)
        logs.append(math.log(variance))
    count = len(values)
    sigma = math.fsum(scaled) / count
    if not sigma > 0.0:
        raise EconometricsInputError("The series' one-step errors are all zero: no variance.")
    log_likelihood = -0.5 * count * (_LOG_TWO_PI + 1.0 + math.log(sigma)) - 0.5 * math.fsum(logs)
    return log_likelihood, errors, sigma


def _conditional_log_likelihood(sum_of_squares: float, count: int) -> float:
    return -0.5 * count * (_LOG_TWO_PI + 1.0 + math.log(sum_of_squares / count))


# --- fitting --------------------------------------------------------------------------


def _choices(tolerance: float, step: float, max_iterations: int) -> None:
    for name, value in (("tolerance", tolerance), ("step", step)):
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not (math.isfinite(value) and value > 0.0)
        ):
            raise EconometricsInputError(f"{name} is a finite positive number, got {value!r}.")
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations < 1
    ):
        raise EconometricsInputError(
            f"max_iterations is a positive integer, got {max_iterations!r}."
        )


def _order(order: object, what: str) -> tuple[int, int]:
    if (
        not isinstance(order, tuple)
        or len(order) != 2
        or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in order)
    ):
        raise EconometricsInputError(f"{what} is (p, q), two non-negative integers, got {order!r}.")
    return order[0], order[1]


def _deterministic(deterministic: object) -> bool:
    if deterministic not in (Deterministic.NONE, Deterministic.CONSTANT):
        raise EconometricsInputError(
            f"An ARMA model is about zero (NONE) or a mean (CONSTANT), got {deterministic!r}."
        )
    return deterministic is Deterministic.CONSTANT


def _start(
    shape: _Shape, start: object, series: Sequence[float]
) -> tuple[list[float], dict[str, Setting]]:
    if start is ArmaStart.ZEROS:
        mean = math.fsum(series) / len(series) if shape.constant else 0.0
        point = shape.point(mean, [0.0] * shape.p, [0.0] * shape.q)
        assert point is not None, "zero coefficients are inside the region"
        return point, {"start": ArmaStart.ZEROS.value}
    if not isinstance(start, Mapping):
        raise EconometricsInputError(f"start is an ArmaStart or a mapping, got {start!r}.")
    names = shape.names
    if sorted(start) != sorted(names):
        raise EconometricsInputError(
            f"An explicit start names {', '.join(names) or 'nothing'}, got {start!r}."
        )
    values = []
    for name in names:
        value = start[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise EconometricsInputError(f"The start's {name} is {value!r}: not a finite number.")
        values.append(float(value))
    point = shape.point(*shape.unflat(values))
    if point is None:
        raise EconometricsInputError(
            "The start is outside the region: its autoregressive polynomial is not stationary "
            "or its moving-average polynomial not invertible."
        )
    return point, {
        "start": "explicit",
        **{f"start_{n}": v for n, v in zip(names, values, strict=True)},
    }


def _jacobian(
    function: Callable[[Sequence[float]], Sequence[float]], point: Sequence[float], step: float
) -> list[list[float]]:
    """``d function / d point`` by central differences, as :func:`numerical_gradient` steps."""

    columns = []
    for index, coordinate in enumerate(point):
        width = step * max(1.0, abs(coordinate))
        up, down = list(point), list(point)
        up[index] = coordinate + width
        down[index] = coordinate - width
        spacing = up[index] - down[index]
        high, low = function(up), function(down)
        columns.append([(a - b) / spacing for a, b in zip(high, low, strict=True)])
    return [[columns[k][i] for k in range(len(point))] for i in range(len(columns[0]))]


def _gauss_newton_errors(
    shape: _Shape,
    series: Sequence[float],
    natural: Sequence[float],
    conditioned: int,
    variance: float,
    step: float,
) -> list[float]:
    """``sqrt(diag(sigma² (J'J)⁻¹))``, ``J`` the residuals' derivatives in the coefficients."""

    def residuals(values: Sequence[float]) -> list[float]:
        return _residuals(series, *shape.unflat(values), conditioned)

    jacobian = _jacobian(residuals, natural, step)
    size = len(natural)
    gram = [
        [math.fsum([row[i] * row[k] for row in jacobian]) for k in range(size)] for i in range(size)
    ]
    try:
        factor = cholesky(gram)
    except NotPositiveDefiniteError as error:
        raise EconometricsInputError(
            "The coefficients are not identified: the residuals' derivatives are dependent, "
            "as a factor common to the two polynomials makes them. Restate the order."
        ) from error
    return [
        math.sqrt(variance * factor.solve([1.0 if j == i else 0.0 for j in range(size)])[i])
        for i in range(size)
    ]


def _fit(
    series: tuple[float, ...],
    p: int,
    q: int,
    constant: bool,
    method: ArmaMethod,
    start: ArmaStart | Mapping[str, float],
    tolerance: float,
    max_iterations: int,
    step: float,
    conditioned: int,
) -> ArmaFit:
    shape = _Shape(p, q, constant)
    point, start_settings = _start(shape, start, series)
    least_squares = method is ArmaMethod.CONDITIONAL_LEAST_SQUARES
    scored = len(series) - conditioned if least_squares else len(series)

    def negative_log_likelihood(coordinates: Sequence[float]) -> float:
        mean, ar, ma = shape.natural(coordinates)
        if least_squares:
            errors = _residuals(series, mean, ar, ma, conditioned)
            return math.fsum([e * e for e in errors])
        return -_exact(series, mean, ar, ma)[0]

    def objective(coordinates: Sequence[float]) -> float:
        return negative_log_likelihood(coordinates) / scored

    convergence = None
    if point:
        minimum = bfgs(
            objective,
            lambda coordinates: numerical_gradient(objective, coordinates, step=step),
            point,
            tolerance=tolerance,
            max_iterations=max_iterations,
        )
        point, convergence = list(minimum.x), minimum.convergence
    mean, ar, ma = shape.natural(point)
    natural = shape.flat(mean, ar, ma)
    if least_squares:
        residuals = _residuals(series, mean, ar, ma, conditioned)
        variance = math.fsum([e * e for e in residuals]) / scored
        if not variance > 0.0:
            raise EconometricsInputError("The residuals are all zero: the model has no variance.")
        log_likelihood = _conditional_log_likelihood(variance * scored, scored)
        errors = (
            _gauss_newton_errors(shape, series, natural, conditioned, variance, step)
            if natural
            else []
        )
    else:
        log_likelihood, residuals, variance = _exact(series, mean, ar, ma)
        errors = []
        if point:
            try:
                covariance = curvature_covariance(
                    lambda coordinates: -_exact(series, *shape.natural(coordinates))[0],
                    point,
                    step,
                )
            except EconometricsInputError as error:
                raise EconometricsInputError(
                    f"{error} A factor common to the two polynomials leaves the coefficients "
                    "unidentified: restate the order."
                ) from error
            jacobian = _jacobian(lambda c: shape.flat(*shape.natural(c)), point, step)
            size = len(point)
            errors = [
                math.sqrt(
                    math.fsum(
                        [
                            jacobian[i][a] * covariance[a][b] * jacobian[i][b]
                            for a in range(size)
                            for b in range(size)
                        ]
                    )
                )
                for i in range(len(natural))
            ]
    names = shape.names
    count = len(names) + 1
    settings: dict[str, Setting] = {
        "ar_order": p,
        "conditioned": conditioned if least_squares else 0,
        "deterministic": Deterministic.CONSTANT.value if constant else Deterministic.NONE.value,
        "ma_order": q,
        "max_iterations": max_iterations,
        "method": method.value,
        "observations": len(series),
        "step": float(step),
        "tolerance": float(tolerance),
        **start_settings,
    }
    estimate = Estimate(
        method=method.scheme,
        data_id=series_identity(series),
        parameters={**dict(zip(names, natural, strict=True)), "variance": variance},
        standard_errors={
            **dict(zip(names, errors, strict=True)),
            "variance": variance * math.sqrt(2.0 / scored),
        },
        settings=settings,
        numerical_class=(
            NumericalClass.CORRECTLY_ROUNDED if least_squares else NumericalClass.HOST_LIBM
        ),
        convergence=convergence,
    )
    return ArmaFit(
        order=(p, q),
        method=method,
        estimate=estimate,
        log_likelihood=log_likelihood,
        residuals=tuple(residuals),
        observations=scored,
        aic=-2.0 * log_likelihood + 2.0 * count,
        bic=-2.0 * log_likelihood + count * math.log(scored),
    )


def _series(values: Sequence[float], conditioned: int) -> tuple[float, ...]:
    return require_series(values, conditioned + MINIMUM_ARMA_OBSERVATIONS, "The series")


def fit_arma(
    values: Sequence[float],
    *,
    order: tuple[int, int],
    deterministic: Deterministic,
    method: ArmaMethod,
    start: ArmaStart | Mapping[str, float],
    tolerance: float,
    max_iterations: int,
    step: float,
) -> ArmaFit:
    """Fit an ARMA(``p``, ``q``) model to ``values``.

    Args:
        values: The series, in time order.
        order: ``(p, q)``.
        deterministic: ``NONE``, about zero, or ``CONSTANT``, about a mean.
        method: Conditional least squares (class 2) or the exact Gaussian
            likelihood (class 3).
        start: :attr:`ArmaStart.ZEROS`, or the mean (with a constant) and
            every coefficient by name, ``ar_i`` and ``ma_j``, inside the region.
        tolerance: BFGS's criterion on the gradient of the mean objective,
            in the optimiser's coordinates.
        max_iterations: BFGS's iterations allowed.
        step: The central differences' step, for the gradient and the
            standard errors.

    Raises:
        EconometricsInputError: If a value is not finite, the series leaves
            fewer than 50 values to score, a choice is malformed, the start is
            outside the region, or the coefficients are not identified.
        ConvergenceError: If BFGS does not meet its criterion.
    """

    _choices(tolerance, step, max_iterations)
    p, q = _order(order, "order")
    constant = _deterministic(deterministic)
    if not isinstance(method, ArmaMethod):
        raise EconometricsInputError(f"method must be an ArmaMethod, got {method!r}.")
    conditioned = p if method is ArmaMethod.CONDITIONAL_LEAST_SQUARES else 0
    series = _series(values, conditioned)
    return _fit(series, p, q, constant, method, start, tolerance, max_iterations, step, conditioned)


def select_arma(
    values: Sequence[float],
    *,
    maximum_order: tuple[int, int],
    criterion: LagCriterion,
    deterministic: Deterministic,
    method: ArmaMethod,
    start: ArmaStart,
    tolerance: float,
    max_iterations: int,
    step: float,
) -> ArmaSelection:
    """Fit every order up to ``maximum_order`` and keep the least ``criterion``'s.

    The grid runs ``p`` from 0 to ``P`` and, within each, ``q`` from 0 to ``Q``;
    a tie goes to the first. Under conditional least squares every candidate
    is conditioned on the first ``P`` values, and the chosen order is refitted
    on all the data it leaves.

    Raises:
        EconometricsInputError: As :func:`fit_arma`, and if the criterion is not
            AIC or BIC or the start is not a named rule.
    """

    _choices(tolerance, step, max_iterations)
    most_ar, most_ma = _order(maximum_order, "maximum_order")
    if criterion not in (LagCriterion.AIC, LagCriterion.BIC):
        raise EconometricsInputError(f"An ARMA order is chosen by AIC or BIC, got {criterion!r}.")
    if not isinstance(start, ArmaStart):
        raise EconometricsInputError(
            f"A selection starts every order by a named rule, got {start!r}."
        )
    constant = _deterministic(deterministic)
    if not isinstance(method, ArmaMethod):
        raise EconometricsInputError(f"method must be an ArmaMethod, got {method!r}.")
    least_squares = method is ArmaMethod.CONDITIONAL_LEAST_SQUARES
    conditioned = most_ar if least_squares else 0
    series = _series(values, conditioned)
    candidates: list[tuple[tuple[int, int], float]] = []
    best: tuple[Decimal | float, int, int] | None = None
    for p in range(most_ar + 1):
        for q in range(most_ma + 1):
            fit = _fit(
                series, p, q, constant, method, start, tolerance, max_iterations, step, conditioned
            )
            count = p + q + int(constant) + 1
            value: Decimal | float
            if least_squares:
                with localcontext() as context:
                    context.prec = 34
                    scored = Decimal(fit.observations)
                    variance = Decimal(fit.estimate.parameters["variance"])
                    penalty = (
                        2 * Decimal(count)
                        if criterion is LagCriterion.AIC
                        else Decimal(count) * scored.ln()
                    )
                    value = scored * variance.ln() + penalty
                candidates.append(((p, q), float(value)))
            else:
                value = fit.aic if criterion is LagCriterion.AIC else fit.bic
                candidates.append(((p, q), value))
            if best is None or value < best[0]:
                best = (value, p, q)
    assert best is not None, "the grid holds (0, 0) at least"
    _, p, q = best
    chosen = _fit(
        _series(values, p if least_squares else 0),
        p,
        q,
        constant,
        method,
        start,
        tolerance,
        max_iterations,
        step,
        p if least_squares else 0,
    )
    return ArmaSelection(criterion=criterion, chosen=chosen, candidates=tuple(candidates))
