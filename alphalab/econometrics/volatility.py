"""Conditional volatility: GARCH, GJR-GARCH and EGARCH, by likelihood (v4.3, ADR-0074, WS-07).

Each model is (1, 1), with a constant mean ``mu`` and Gaussian errors. With
``e_t = r_t - mu`` and ``h_t`` the conditional variance:

* **GARCH**: ``h_t = omega + alpha e²_{t-1} + beta h_{t-1}``.
* **GJR**: ``h_t = omega + (alpha + gamma 1[e_{t-1} < 0]) e²_{t-1} + beta h_{t-1}``: bad news
  moves the variance more when ``gamma > 0``.
* **EGARCH**: ``ln h_t = omega + alpha (|z_{t-1}| - sqrt(2/pi)) + gamma z_{t-1} + beta ln h_{t-1}``,
  with ``z = e / sqrt(h)``.

Every choice is stated
----------------------
The start, as values or the named rule :attr:`VolatilityStart.MOMENTS`; the
optimiser's tolerance and limit; the step of its differences; and ``h_0``, the
sample variance of the returns, recorded as a setting. Nothing is defaulted.

Admissible by construction
--------------------------
The optimiser moves in unconstrained coordinates that map onto the admissible
region only (ADR-0074 section 4): GARCH's ``alpha, beta > 0`` with persistence
``alpha + beta`` below one, GJR's ``alpha, gamma/2, beta`` sharing a persistence below one, and
EGARCH's ``|beta| < 1``. A start outside the region is refused.

Classes
-------
The likelihood is ``log`` and EGARCH's recursion ``exp``: class 3. The estimate
is recorded beside its identity, with the optimiser's convergence report
(ADR-0058). Standard errors invert the likelihood's curvature at the maximum,
by central differences; a curvature that is not positive definite is refused,
as no maximum to report. At the region's edge -- GJR's ``gamma`` at zero, which
is the GARCH it nests -- a standard error is the curvature's alone: the normal
approximation it is read by does not hold at a boundary.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from alphalab.common.estimate import Estimate, NumericalClass, Setting
from alphalab.common.linalg import NotPositiveDefiniteError, cholesky
from alphalab.common.optimize import bfgs, numerical_gradient
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import require_series, series_identity

__all__ = [
    "MINIMUM_VOLATILITY_OBSERVATIONS",
    "VOLATILITY_METHOD",
    "VolatilityFit",
    "VolatilityModel",
    "VolatilityStart",
    "fit_volatility",
]

#: The method scheme: Gaussian likelihood, maximised by BFGS in admissible coordinates.
VOLATILITY_METHOD: Final = "alphalab.econometrics.volatility.fit_volatility/gaussian-bfgs.v1"
#: The fewest returns a volatility model is fitted to.
MINIMUM_VOLATILITY_OBSERVATIONS: Final = 100

_EXPECTED_ABSOLUTE: Final = math.sqrt(2.0 / math.pi)


class VolatilityModel(Enum):
    """Which conditional-variance recursion."""

    GARCH = "garch"
    GJR = "gjr"
    EGARCH = "egarch"


class VolatilityStart(Enum):
    """A named rule for the optimiser's start."""

    #: ``mu`` the sample mean, and ``omega`` setting the unconditional variance to the
    #: sample's. GARCH: ``alpha = 0.05``, ``beta = 0.85``. GJR: ``alpha = 0.03``,
    #: ``gamma = 0.04``, ``beta = 0.85``. EGARCH: ``alpha = 0.1``, ``gamma = 0``, ``beta = 0.9``.
    MOMENTS = "moments"


@dataclass(frozen=True, slots=True)
class VolatilityFit:
    """A fitted conditional-variance model.

    Attributes:
        model: The recursion.
        estimate: ``mu``, ``omega``, ``alpha``, ``gamma`` (GJR and EGARCH) and
            ``beta``, with standard errors, class 3, with the optimiser's report.
        log_likelihood: The Gaussian log-likelihood at the estimate.
        variances: ``h_t`` at each return, in time order.
        persistence: ``alpha + beta`` (GARCH), ``alpha + gamma/2 + beta`` (GJR) or
            ``beta`` (EGARCH).
    """

    model: VolatilityModel
    estimate: Estimate
    log_likelihood: float
    variances: tuple[float, ...]
    persistence: float


def fit_volatility(
    returns: Sequence[float],
    *,
    model: VolatilityModel,
    start: VolatilityStart | Mapping[str, float],
    tolerance: float,
    max_iterations: int,
    step: float,
) -> VolatilityFit:
    """Fit ``model`` to ``returns`` by Gaussian maximum likelihood.

    Args:
        returns: The returns, in time order.
        model: GARCH, GJR or EGARCH, each (1, 1).
        start: The optimiser's first point: :attr:`VolatilityStart.MOMENTS`, or
            every parameter of the model by name.
        tolerance: BFGS's criterion on the gradient of the mean negative
            log-likelihood.
        max_iterations: BFGS's iterations allowed.
        step: The step of the central differences, for the gradient and the
            curvature, in the optimiser's coordinates.

    Raises:
        EconometricsInputError: If a value is not finite, there are fewer than
            100 returns, the start is outside the admissible region or names the
            wrong parameters, or the curvature at the maximum is not positive
            definite.
        ConvergenceError: If BFGS does not meet its criterion.
    """

    if not isinstance(model, VolatilityModel):
        raise EconometricsInputError(f"model must be a VolatilityModel, got {model!r}.")
    for name, value in (("tolerance", tolerance), ("step", step)):
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not (math.isfinite(value) and value > 0.0)
        ):
            raise EconometricsInputError(f"{name} is a finite positive number, got {value!r}.")
    values = require_series(returns, MINIMUM_VOLATILITY_OBSERVATIONS, "The returns")
    count = len(values)
    mean = math.fsum(values) / count
    variance = math.fsum((v - mean) ** 2 for v in values) / count
    if variance == 0.0:
        raise EconometricsInputError("The returns do not vary: there is no volatility to model.")

    names = _names(model)
    natural = _start(model, start, mean, variance, names)
    coordinates = _to_coordinates(model, natural)

    def objective(point: Sequence[float]) -> float:
        return -_log_likelihood(model, _from_coordinates(model, point), values, variance) / count

    minimum = bfgs(
        objective,
        lambda point: numerical_gradient(objective, point, step=step),
        coordinates,
        tolerance=tolerance,
        max_iterations=max_iterations,
    )
    parameters = _from_coordinates(model, minimum.x)
    log_likelihood, variances = _recursion(model, parameters, values, variance)
    errors = _standard_errors(model, parameters, values, variance, step)
    persistence = _persistence(model, parameters)
    settings: dict[str, Setting] = {
        "model": model.value,
        "initial_variance": "sample_variance",
        "max_iterations": max_iterations,
        "observations": count,
        "step": float(step),
        "tolerance": float(tolerance),
        "start": start.value if isinstance(start, VolatilityStart) else "explicit",
    }
    if not isinstance(start, VolatilityStart):
        settings.update({f"start_{name}": float(start[name]) for name in names})
    estimate = Estimate(
        method=VOLATILITY_METHOD,
        data_id=series_identity(values),
        parameters=dict(zip(names, parameters, strict=True)),
        standard_errors=dict(zip(names, errors, strict=True)),
        settings=settings,
        numerical_class=NumericalClass.HOST_LIBM,
        convergence=minimum.convergence,
    )
    return VolatilityFit(model, estimate, log_likelihood, tuple(variances), persistence)


def _names(model: VolatilityModel) -> list[str]:
    if model is VolatilityModel.GARCH:
        return ["mu", "omega", "alpha", "beta"]
    return ["mu", "omega", "alpha", "gamma", "beta"]


def _persistence(model: VolatilityModel, parameters: Sequence[float]) -> float:
    if model is VolatilityModel.GARCH:
        _, _, alpha, beta = parameters
        return alpha + beta
    _, _, alpha, gamma, beta = parameters
    if model is VolatilityModel.GJR:
        return alpha + gamma / 2.0 + beta
    return beta


def _start(
    model: VolatilityModel,
    start: VolatilityStart | Mapping[str, float],
    mean: float,
    variance: float,
    names: list[str],
) -> list[float]:
    if isinstance(start, VolatilityStart):
        if model is VolatilityModel.GARCH:
            return [mean, variance * (1.0 - 0.9), 0.05, 0.85]
        if model is VolatilityModel.GJR:
            return [mean, variance * (1.0 - 0.9), 0.03, 0.04, 0.85]
        return [mean, math.log(variance) * (1.0 - 0.9), 0.1, 0.0, 0.9]
    if not isinstance(start, Mapping) or sorted(start) != sorted(names):
        raise EconometricsInputError(f"A {model.value} start names {sorted(names)}, got {start!r}.")
    point = []
    for name in names:
        value = start[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise EconometricsInputError(f"The start's {name} is a finite number, got {value!r}.")
        point.append(float(value))
    if model is not VolatilityModel.EGARCH:
        omega, alpha = point[1], point[2]
        beta = point[-1]
        gamma = point[3] if model is VolatilityModel.GJR else 0.0
        persistence = alpha + gamma / 2.0 + beta
        if not (omega > 0.0 and alpha > 0.0 and beta > 0.0 and gamma >= 0.0 and persistence < 1.0):
            raise EconometricsInputError(
                f"The start {dict(zip(names, point, strict=True))} is outside the admissible "
                "region: omega, alpha and beta positive, gamma non-negative, persistence below one."
            )
        if model is VolatilityModel.GJR and gamma == 0.0:
            raise EconometricsInputError(
                "A GJR start's gamma is positive: its coordinates share the persistence among "
                "alpha, gamma / 2 and beta."
            )
    elif not -1.0 < point[-1] < 1.0:
        raise EconometricsInputError(
            f"An EGARCH start's beta is inside (-1, 1), got {point[-1]!r}."
        )
    return point


def _logit(value: float) -> float:
    return math.log(value / (1.0 - value))


def _logistic(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _to_coordinates(model: VolatilityModel, natural: Sequence[float]) -> tuple[float, ...]:
    if model is VolatilityModel.GARCH:
        mu, omega, alpha, beta = natural
        persistence = alpha + beta
        return (mu, math.log(omega), _logit(persistence), _logit(alpha / persistence))
    if model is VolatilityModel.GJR:
        mu, omega, alpha, gamma, beta = natural
        half = gamma / 2.0
        persistence = alpha + half + beta
        return (
            mu,
            math.log(omega),
            _logit(persistence),
            math.log(alpha / beta),
            math.log(half / beta),
        )
    mu, omega, alpha, gamma, beta = natural
    return (mu, omega, alpha, gamma, math.atanh(beta))


def _from_coordinates(model: VolatilityModel, point: Sequence[float]) -> list[float]:
    if model is VolatilityModel.GARCH:
        mu, log_omega, persistence, share = point
        p, s = _logistic(persistence), _logistic(share)
        return [mu, math.exp(log_omega), p * s, p * (1.0 - s)]
    if model is VolatilityModel.GJR:
        mu, log_omega, persistence, alpha_share, half_share = point
        p = _logistic(persistence)
        weights = [math.exp(alpha_share), math.exp(half_share), 1.0]
        total = math.fsum(weights)
        alpha, half, beta = (p * w / total for w in weights)
        return [mu, math.exp(log_omega), alpha, 2.0 * half, beta]
    mu, omega, alpha, gamma, beta = point
    return [mu, omega, alpha, gamma, math.tanh(beta)]


def _recursion(
    model: VolatilityModel, parameters: Sequence[float], values: Sequence[float], initial: float
) -> tuple[float, list[float]]:
    """The log-likelihood and the conditional variances at ``parameters``."""

    total = 0.0
    variances: list[float] = []
    log_two_pi = math.log(2.0 * math.pi)
    if model is VolatilityModel.EGARCH:
        mu, omega, alpha, gamma, beta = parameters
        log_h = math.log(initial)
        previous: float | None = None
        for value in values:
            if previous is not None:
                log_h = (
                    omega
                    + alpha * (abs(previous) - _EXPECTED_ABSOLUTE)
                    + gamma * previous
                    + beta * log_h
                )
            h = math.exp(log_h)
            e = value - mu
            variances.append(h)
            total += log_two_pi + log_h + e * e / h
            previous = e / math.sqrt(h)
        return -0.5 * total, variances
    if model is VolatilityModel.GARCH:
        mu, omega, alpha, beta = parameters
        gamma = 0.0
    else:
        mu, omega, alpha, gamma, beta = parameters
    h = initial
    last: float | None = None
    for value in values:
        if last is not None:
            weight = alpha + (gamma if last < 0.0 else 0.0)
            h = omega + weight * last * last + beta * h
        e = value - mu
        variances.append(h)
        total += log_two_pi + math.log(h) + e * e / h
        last = e
    return -0.5 * total, variances


def _log_likelihood(
    model: VolatilityModel, parameters: Sequence[float], values: Sequence[float], initial: float
) -> float:
    return _recursion(model, parameters, values, initial)[0]


def _standard_errors(
    model: VolatilityModel,
    parameters: Sequence[float],
    values: Sequence[float],
    initial: float,
    step: float,
) -> list[float]:
    """``sqrt(diag(H⁻¹))``, ``H`` the negative log-likelihood's central-difference curvature."""

    size = len(parameters)
    scales = [step * max(1.0, abs(p)) for p in parameters]

    def f(point: Sequence[float]) -> float:
        return -_log_likelihood(model, point, values, initial)

    def moved(changes: Mapping[int, float]) -> list[float]:
        point = list(parameters)
        for index, change in changes.items():
            point[index] += change
        return point

    centre = f(parameters)
    hessian = [[0.0] * size for _ in range(size)]
    for i in range(size):
        hi = scales[i]
        hessian[i][i] = (f(moved({i: hi})) - 2.0 * centre + f(moved({i: -hi}))) / (hi * hi)
        for k in range(i):
            hk = scales[k]
            value = (
                f(moved({i: hi, k: hk}))
                - f(moved({i: hi, k: -hk}))
                - f(moved({i: -hi, k: hk}))
                + f(moved({i: -hi, k: -hk}))
            ) / (4.0 * hi * hk)
            hessian[i][k] = hessian[k][i] = value
    try:
        factor = cholesky(hessian)
    except NotPositiveDefiniteError as error:
        raise EconometricsInputError(
            "The likelihood's curvature at the optimum is not positive definite: the point "
            "is no maximum to report standard errors for. Restate the start or the model."
        ) from error
    errors = []
    for i in range(size):
        unit = [1.0 if j == i else 0.0 for j in range(size)]
        errors.append(math.sqrt(factor.solve(unit)[i]))
    return errors
