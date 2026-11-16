"""Linear Gaussian state space: the Kalman filter and the RTS smoother (v4.3, ADR-0074, WS-07).

The model is

* ``x_t = F x_{t-1} + w_t``, with ``w_t ~ N(0, Q)``: the state's transition;
* ``y_t = H_t x_t + v_t``, with ``v_t ~ N(0, R)``: what is observed;
* ``x_0 ~ N(m_0, P_0)``: the state before the first observation.

``H_t`` is given for every observation: a regression whose coefficients drift,
such as a pair's hedge ratio, is ``F = I`` with ``H_t`` the regressors at ``t``.

The filter
----------
:func:`kalman_filter` predicts, ``m⁻ = F m`` and ``P⁻ = F P F' + Q``; measures
the innovation ``e = y - H m⁻`` and its covariance ``S = H P⁻ H' + R``; and
updates by the gain ``K = P⁻ H' S⁻¹``: ``m = m⁻ + K e``. The covariance takes
Joseph's form, ``P = (I - K H) P⁻ (I - K H)' + K R K'``, which stays symmetric
and positive semi-definite under rounding where the short form need not. A
scalar ``S`` divides; a matrix is factored by M2's Cholesky, never inverted.

The smoother
------------
:func:`rts_smoother` runs Rauch, Tung and Striebel's recursion backward over the
filter's output, to ``x_0``: ``C = P F' (P⁻₊)⁻¹``, ``m_s = m + C (m_s₊ - m⁻₊)``
and ``P_s = P + C (P_s₊ - P⁻₊) C'``, where ``₊`` marks the next step. Its means
are each state's given every observation.

Two models fitted by likelihood
-------------------------------
* :func:`fit_local_level`: ``y_t = mu_t + v_t`` with ``mu_t = mu_{t-1} + w_t``, a
  level wandering under noise (Durbin and Koopman, 2012, chapter 2).
* :func:`fit_time_varying_beta`: ``y_t = h_t' b_t + v_t``, each coefficient in
  ``b_t`` a random walk with a variance of its own: a beta or a hedge ratio
  that drifts.

Neither states a prior for its first state. It is diffuse -- unknown, with no
prior -- and conditioned on the first ``k`` observations, ``k`` the state's
size, which fix it: its mean solves ``H_{1..k} x_k = y_{1..k}``, and its
covariance is ``A Omega A'``, with ``A`` that system's inverse and ``Omega`` the
covariance of the first observations' errors about ``x_k``: their noise, and
the drift between each and observation ``k``. The likelihood maximised is that
of the observations after the first ``k``, given them. Since ``F`` and ``H``
hold no parameter, the terms the exact diffuse likelihood gives the first
``k`` observations hold none either (Durbin and Koopman, 2012, chapters 5 and
7), so the two likelihoods have the same maximum.

Classes
-------
Means and covariances are ``+ - * /``, square roots and ``fsum``: class 2, the
same bits on every host for given parameters. The log-likelihood is ``log``:
class 3, beside them. A fit's variances maximise it, so its estimate is class
3, with the optimiser's report. BFGS moves in the variances' logarithms, so
every variance stays positive at any scale; the standard errors invert the
likelihood's curvature in those logarithms and map back by the delta method,
``se(v) = v se(ln v)``. A variance near zero -- a level or coefficient that
hardly drifts -- is near the region's edge, where the normal approximation
that reads a standard error does not hold.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from alphalab.common.estimate import Estimate, NumericalClass, Setting
from alphalab.common.linalg import (
    CholeskyFactor,
    IllConditionedError,
    NotPositiveDefiniteError,
    cholesky,
    least_squares,
)
from alphalab.common.optimize import bfgs, numerical_gradient
from alphalab.econometrics._likelihood import curvature_standard_errors
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import require_series, series_identity, system_identity

__all__ = [
    "LOCAL_LEVEL_METHOD",
    "MINIMUM_STATE_SPACE_OBSERVATIONS",
    "TIME_VARYING_BETA_METHOD",
    "Intercept",
    "KalmanFilter",
    "LinearGaussianModel",
    "RtsSmoother",
    "StateSpaceFit",
    "fit_local_level",
    "fit_time_varying_beta",
    "kalman_filter",
    "rts_smoother",
]

#: The local level's method scheme: the diffuse likelihood by the filter, maximised by BFGS.
LOCAL_LEVEL_METHOD: Final = "alphalab.econometrics.state_space.fit_local_level/diffuse-bfgs.v1"

#: The time-varying beta's method scheme, as the local level's.
TIME_VARYING_BETA_METHOD: Final = (
    "alphalab.econometrics.state_space.fit_time_varying_beta/diffuse-bfgs.v1"
)

#: The observations a fit's likelihood needs, after the ``k`` its start is conditioned on.
MINIMUM_STATE_SPACE_OBSERVATIONS: Final = 20

_LOG_TWO_PI: Final = math.log(2.0 * math.pi)

_Matrix = tuple[tuple[float, ...], ...]
_Vector = tuple[float, ...]


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EconometricsInputError(f"{what} holds {value!r}: not a number.")
    number = float(value)
    if not math.isfinite(number):
        raise EconometricsInputError(f"{what} holds {value!r}: not finite.")
    return number


def _matrix(rows: object, what: str, height: int, width: int) -> _Matrix:
    if (
        not isinstance(rows, Sequence)
        or len(rows) != height
        or any(not isinstance(row, Sequence) or len(row) != width for row in rows)
    ):
        raise EconometricsInputError(f"{what} is {height} x {width}, got {rows!r}.")
    return tuple(tuple(_number(value, what) for value in row) for row in rows)


def _covariance(matrix: _Matrix, what: str) -> None:
    for i, row in enumerate(matrix):
        if row[i] < 0.0:
            raise EconometricsInputError(f"{what} has a negative variance on its diagonal.")
        for k in range(i):
            if row[k] != matrix[k][i]:
                raise EconometricsInputError(f"{what} is a covariance, so symmetric.")


class Intercept(Enum):
    """A time-varying regression's intercept: absent, fixed, or drifting as its betas do."""

    NONE = "none"
    #: In the model, constant: its drift's variance is zero, stated rather than estimated.
    FIXED = "fixed"
    #: In the model, a random walk with a variance of its own.
    DRIFTING = "drifting"


@dataclass(frozen=True, slots=True)
class LinearGaussianModel:
    """``x_t = F x_{t-1} + w_t`` and ``y_t = H_t x_t + v_t``, from ``x_0 ~ N(m_0, P_0)``.

    ``Q`` and ``P_0`` are checked symmetric with a non-negative diagonal; that
    they are positive semi-definite is the caller's to hold, as a diagonal is.

    Attributes:
        transition: ``F``, ``k x k``.
        transition_covariance: ``Q``, ``k x k``. A zero variance is a state
            that does not drift.
        observation_covariance: ``R``, ``n x n``, positive definite.
        initial_mean: ``m_0``, the ``k`` values of the state before the first
            observation.
        initial_covariance: ``P_0``, ``k x k``.

    Raises:
        EconometricsInputError: If a shape is wrong or a value not finite, a
            covariance is not symmetric or has a negative variance, or ``R`` is
            not positive definite.
    """

    transition: _Matrix
    transition_covariance: _Matrix
    observation_covariance: _Matrix
    initial_mean: _Vector
    initial_covariance: _Matrix

    def __post_init__(self) -> None:
        if not isinstance(self.initial_mean, Sequence) or not self.initial_mean:
            raise EconometricsInputError("m_0 is the state's values, one at least.")
        if not isinstance(self.observation_covariance, Sequence) or not (
            self.observation_covariance
        ):
            raise EconometricsInputError("R is n x n for n observed values, one at least.")
        size, observed = len(self.initial_mean), len(self.observation_covariance)
        fields = {
            "transition": _matrix(self.transition, "F", size, size),
            "transition_covariance": _matrix(self.transition_covariance, "Q", size, size),
            "observation_covariance": _matrix(self.observation_covariance, "R", observed, observed),
            "initial_mean": _matrix([self.initial_mean], "m_0", 1, size)[0],
            "initial_covariance": _matrix(self.initial_covariance, "P_0", size, size),
        }
        for name, value in fields.items():
            object.__setattr__(self, name, value)
        _covariance(self.transition_covariance, "Q")
        _covariance(self.observation_covariance, "R")
        _covariance(self.initial_covariance, "P_0")
        try:
            cholesky(self.observation_covariance)
        except NotPositiveDefiniteError as error:
            raise EconometricsInputError(
                "R is the observations' covariance, so positive definite: a value observed "
                "without error is a state, not an observation."
            ) from error


@dataclass(frozen=True, slots=True)
class KalmanFilter:
    """The filter's pass over the observations, one entry per observation.

    Attributes:
        predicted_means: ``m⁻_t``, each state given the observations before it.
        predicted_covariances: ``P⁻_t``.
        filtered_means: ``m_t``, each state given the observations up to it.
        filtered_covariances: ``P_t``.
        innovations: ``e_t = y_t - H_t m⁻_t``.
        innovation_covariances: ``S_t``.
        log_likelihood: ``sum log N(e_t; 0, S_t)``, class 3.
    """

    predicted_means: tuple[_Vector, ...]
    predicted_covariances: tuple[_Matrix, ...]
    filtered_means: tuple[_Vector, ...]
    filtered_covariances: tuple[_Matrix, ...]
    innovations: tuple[_Vector, ...]
    innovation_covariances: tuple[_Matrix, ...]
    log_likelihood: float


@dataclass(frozen=True, slots=True)
class RtsSmoother:
    """Each state given every observation.

    Attributes:
        smoothed_means: ``m_{t|T}``, one per observation.
        smoothed_covariances: ``P_{t|T}``.
        initial_mean: ``m_{0|T}``: the state before the first observation.
        initial_covariance: ``P_{0|T}``.
    """

    smoothed_means: tuple[_Vector, ...]
    smoothed_covariances: tuple[_Matrix, ...]
    initial_mean: _Vector
    initial_covariance: _Matrix


@dataclass(frozen=True, slots=True)
class StateSpaceFit:
    """A state-space model fitted by likelihood, with its states at the estimate.

    Its states are the filter's and the smoother's at the estimate: class 2
    for the variances, which are class 3.

    Attributes:
        estimate: The variances and their standard errors, class 3, with the
            optimiser's report.
        log_likelihood: Of the observations after the first ``conditioned``,
            given them, at the estimate.
        conditioned: ``k``, the first observations the start is conditioned on.
        model: The model at the estimate. Its initial state is the state at
            observation ``k``, given the first ``k``.
        filtered: The filter over the observations after the first ``k``.
        smoothed: The smoother over them, back to observation ``k``.
    """

    estimate: Estimate
    log_likelihood: float
    conditioned: int
    model: LinearGaussianModel
    filtered: KalmanFilter
    smoothed: RtsSmoother

    @property
    def filtered_states(self) -> tuple[_Vector, ...]:
        """The state at each observation from the ``k``-th, given the observations up to it."""

        return (self.model.initial_mean, *self.filtered.filtered_means)

    @property
    def smoothed_states(self) -> tuple[_Vector, ...]:
        """The state at each observation from the ``k``-th, given every observation."""

        return (self.smoothed.initial_mean, *self.smoothed.smoothed_means)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    """``a . b``, exactly rounded; the lengths are the callers' to match."""

    return math.fsum(map(operator.mul, a, b))


def _times(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> list[list[float]]:
    columns = list(zip(*b, strict=True))
    return [[_dot(row, column) for column in columns] for row in a]


def _transpose(a: Sequence[Sequence[float]]) -> list[list[float]]:
    return [list(column) for column in zip(*a, strict=True)]


def _plus(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> list[list[float]]:
    return [[x + y for x, y in zip(r, s, strict=True)] for r, s in zip(a, b, strict=True)]


def _minus(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> list[list[float]]:
    return [[x - y for x, y in zip(r, s, strict=True)] for r, s in zip(a, b, strict=True)]


def _symmetrize(a: Sequence[Sequence[float]]) -> _Matrix:
    """``(A + A') / 2``: a covariance's symmetry, which rounding can disturb, restored."""

    size = len(a)
    return tuple(tuple((a[i][k] + a[k][i]) / 2.0 for k in range(size)) for i in range(size))


def _identity(size: int) -> _Matrix:
    return tuple(tuple(1.0 if i == k else 0.0 for k in range(size)) for i in range(size))


class _Inverse:
    """``S⁻¹`` applied, ``S`` symmetric positive definite: a division for a number, else solves.

    A matrix is factored by M2's Cholesky, and each vector solved through it.
    """

    __slots__ = ("_factor", "_variance")

    def __init__(self, s: _Matrix, refusal: str) -> None:
        self._variance = 0.0
        self._factor: CholeskyFactor | None = None
        if len(s) == 1:
            if not s[0][0] > 0.0:
                raise EconometricsInputError(refusal)
            self._variance = s[0][0]
            return
        try:
            self._factor = cholesky(s)
        except NotPositiveDefiniteError as error:
            raise EconometricsInputError(refusal) from error

    def apply(self, vector: Sequence[float]) -> _Vector:
        if self._factor is None:
            return (vector[0] / self._variance,)
        return self._factor.solve(vector)

    def log_determinant(self) -> float:
        if self._factor is None:
            return math.log(self._variance)
        return math.fsum([math.log(pivot) for pivot in self._factor.pivots])


def kalman_filter(
    model: LinearGaussianModel,
    observations: Sequence[Sequence[float]],
    observation_matrices: Sequence[Sequence[Sequence[float]]],
) -> KalmanFilter:
    """Filter ``observations`` through ``model``.

    Args:
        model: The transition, the covariances and the state before the first
            observation.
        observations: ``y_t``, each ``n`` finite values, in time order.
        observation_matrices: ``H_t``, one ``n x k`` matrix per observation.

    Raises:
        EconometricsInputError: If there is no observation, a shape is wrong
            or a value not finite, or an innovation's covariance is not
            positive definite.
    """

    if not isinstance(model, LinearGaussianModel):
        raise EconometricsInputError(f"model must be a LinearGaussianModel, got {model!r}.")
    size, observed = len(model.initial_mean), len(model.observation_covariance)
    if not isinstance(observations, Sequence) or not observations:
        raise EconometricsInputError("A filter needs one observation at least.")
    if not isinstance(observation_matrices, Sequence) or len(observation_matrices) != len(
        observations
    ):
        raise EconometricsInputError(
            f"H is given for every observation: {len(observations)} observations need as "
            "many matrices."
        )
    ys = [_matrix([y], f"Observation {t + 1}", 1, observed)[0] for t, y in enumerate(observations)]
    hs = [
        _matrix(h, f"H at observation {t + 1}", observed, size)
        for t, h in enumerate(observation_matrices)
    ]
    return _filter(model, ys, hs)


def _filter(
    model: LinearGaussianModel, ys: Sequence[_Vector], hs: Sequence[_Matrix]
) -> KalmanFilter:
    f, q, r = model.transition, model.transition_covariance, model.observation_covariance
    f_transpose = _transpose(f)
    identity = _identity(len(f))
    # A random walk's prediction moves nothing: F m is m, and F P F' + Q is P + Q, the
    # same numbers the products give, since P and Q are exactly symmetric.
    walk = f == identity
    scalar = len(r) == 1
    mean, covariance = model.initial_mean, model.initial_covariance
    predicted_means: list[_Vector] = []
    predicted_covariances: list[_Matrix] = []
    filtered_means: list[_Vector] = []
    filtered_covariances: list[_Matrix] = []
    innovations: list[_Vector] = []
    innovation_covariances: list[_Matrix] = []
    terms = []
    for t, (y, h) in enumerate(zip(ys, hs, strict=True)):
        if walk:
            prior_mean = mean
            prior = tuple(
                tuple(p + w for p, w in zip(p_row, q_row, strict=True))
                for p_row, q_row in zip(covariance, q, strict=True)
            )
        else:
            prior_mean = tuple(_dot(row, mean) for row in f)
            prior = _symmetrize(_plus(_times(_times(f, covariance), f_transpose), q))
        innovation = tuple(value - _dot(row, prior_mean) for value, row in zip(y, h, strict=True))
        cross = _times(prior, _transpose(h))  # P⁻ H'
        s = _symmetrize(_plus(_times(h, cross), r))
        inverse = _Inverse(
            s, f"The innovation's covariance at observation {t + 1} is not positive definite."
        )
        # K = P⁻ H' S⁻¹, row by row, S being symmetric.
        gain = [inverse.apply(row) for row in cross]
        mean = tuple(m + _dot(k_row, innovation) for m, k_row in zip(prior_mean, gain, strict=True))
        if scalar:
            # The products below, each of one term, written out: the same numbers.
            k = [row[0] for row in gain]
            reduction: Sequence[Sequence[float]] = [
                [e - k_i * h_j for e, h_j in zip(i_row, h[0], strict=True)]
                for k_i, i_row in zip(k, identity, strict=True)
            ]
            noise = [[(k_i * r[0][0]) * k_j for k_j in k] for k_i in k]
        else:
            reduction = _minus(identity, _times(gain, h))
            noise = _times(_times(gain, r), _transpose(gain))
        covariance = _symmetrize(
            _plus(_times(_times(reduction, prior), _transpose(reduction)), noise)
        )
        terms.append(
            len(y) * _LOG_TWO_PI
            + inverse.log_determinant()
            + _dot(innovation, inverse.apply(innovation))
        )
        predicted_means.append(prior_mean)
        predicted_covariances.append(prior)
        filtered_means.append(mean)
        filtered_covariances.append(covariance)
        innovations.append(innovation)
        innovation_covariances.append(s)
    return KalmanFilter(
        predicted_means=tuple(predicted_means),
        predicted_covariances=tuple(predicted_covariances),
        filtered_means=tuple(filtered_means),
        filtered_covariances=tuple(filtered_covariances),
        innovations=tuple(innovations),
        innovation_covariances=tuple(innovation_covariances),
        log_likelihood=-0.5 * math.fsum(terms),
    )


def rts_smoother(model: LinearGaussianModel, filtered: KalmanFilter) -> RtsSmoother:
    """Rauch, Tung and Striebel's smoother over ``filtered``, ``model``'s filter output.

    Raises:
        EconometricsInputError: If ``filtered`` is not a filter of a state of
            ``model``'s size, or a predicted covariance is not positive
            definite: a state predicted with no uncertainty cannot be smoothed
            back through.
    """

    if not isinstance(model, LinearGaussianModel):
        raise EconometricsInputError(f"model must be a LinearGaussianModel, got {model!r}.")
    if not isinstance(filtered, KalmanFilter):
        raise EconometricsInputError(f"filtered must be a KalmanFilter, got {filtered!r}.")
    size = len(model.initial_mean)
    if any(len(mean) != size for mean in filtered.filtered_means):
        raise EconometricsInputError(
            f"The filter's states are not of the model's size, {size}: it is another model's."
        )
    f_transpose = _transpose(model.transition)
    states = [
        (model.initial_mean, model.initial_covariance),
        *zip(filtered.filtered_means, filtered.filtered_covariances, strict=True),
    ]
    later_mean, later_covariance = states[-1]
    means, covariances = [later_mean], [later_covariance]
    for j in range(len(states) - 2, -1, -1):
        mean, covariance = states[j]
        inverse = _Inverse(
            filtered.predicted_covariances[j],
            f"The prediction for observation {j + 1} is not positive definite: it carries no "
            "uncertainty to smooth back through.",
        )
        # C = P F' (P⁻₊)⁻¹, row by row, P⁻₊ being symmetric.
        gain = [inverse.apply(row) for row in _times(covariance, f_transpose)]
        difference = [a - b for a, b in zip(later_mean, filtered.predicted_means[j], strict=True)]
        later_mean = tuple(m + _dot(c_row, difference) for m, c_row in zip(mean, gain, strict=True))
        spread = _minus(later_covariance, filtered.predicted_covariances[j])
        later_covariance = _symmetrize(
            _plus(covariance, _times(_times(gain, spread), _transpose(gain)))
        )
        means.append(later_mean)
        covariances.append(later_covariance)
    means.reverse()
    covariances.reverse()
    return RtsSmoother(
        smoothed_means=tuple(means[1:]),
        smoothed_covariances=tuple(covariances[1:]),
        initial_mean=means[0],
        initial_covariance=covariances[0],
    )


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


def _start_values(start: object, names: Sequence[str]) -> list[float]:
    if not isinstance(start, Mapping) or sorted(start) != sorted(names):
        raise EconometricsInputError(
            f"The start names each variance, {', '.join(names)}, got {start!r}."
        )
    values = []
    for name in names:
        value = start[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not (math.isfinite(value) and value > 0.0)
        ):
            raise EconometricsInputError(
                f"The start's {name} is a variance, a finite positive number, got {value!r}."
            )
        values.append(float(value))
    return values


@dataclass(frozen=True, slots=True)
class _RandomWalks:
    """Coefficients ``b_t = b_{t-1} + w_t``, seen through ``y_t = h_t' b_t + v_t``, diffuse.

    Attributes:
        rows: ``h_t``, every observation's.
        values: ``y_t``.
        start_mean: The coefficients at observation ``k``, given the first ``k``.
        inverse: ``A``, the first ``k`` rows' inverse.
    """

    rows: tuple[_Vector, ...]
    values: _Vector
    start_mean: _Vector
    inverse: _Matrix

    @property
    def size(self) -> int:
        return len(self.start_mean)

    def model(self, drifts: Sequence[float], noise: float) -> LinearGaussianModel:
        size = self.size
        first = self.rows[:size]
        # Omega_ts: the noise, and the drift between observation t and observation k.
        omega = [
            [
                (size - 1 - max(t, s))
                * math.fsum([first[t][j] * first[s][j] * drifts[j] for j in range(size)])
                + (noise if t == s else 0.0)
                for s in range(size)
            ]
            for t in range(size)
        ]
        inverse = self.inverse
        return LinearGaussianModel(
            transition=_identity(size),
            transition_covariance=tuple(
                tuple(drifts[i] if i == j else 0.0 for j in range(size)) for i in range(size)
            ),
            observation_covariance=((noise,),),
            initial_mean=self.start_mean,
            initial_covariance=_symmetrize(_times(_times(inverse, omega), _transpose(inverse))),
        )

    def filter(self, model: LinearGaussianModel) -> KalmanFilter:
        size = self.size
        return _filter(
            model,
            [(value,) for value in self.values[size:]],
            [(row,) for row in self.rows[size:]],
        )


def _random_walks(
    rows: Sequence[_Vector], values: _Vector, maximum_condition: float
) -> _RandomWalks:
    size = len(rows[0])
    first = [list(row) for row in rows[:size]]
    try:
        mean = least_squares(first, values[:size], maximum_condition).coefficients
        columns = [
            least_squares(
                first, [1.0 if i == j else 0.0 for i in range(size)], maximum_condition
            ).coefficients
            for j in range(size)
        ]
    except IllConditionedError as error:
        raise IllConditionedError(
            f"The first {size} observations fix the start, and their regressors are "
            f"conditioned at {error.condition:.6g}, beyond the stated maximum of "
            f"{maximum_condition:g}: begin the series where they are not (nearly) dependent.",
            error.condition,
            error.maximum,
        ) from error
    inverse = tuple(tuple(columns[j][i] for j in range(size)) for i in range(size))
    return _RandomWalks(tuple(rows), values, mean, inverse)


def _fit(
    walks: _RandomWalks,
    drifting: Sequence[bool],
    names: Sequence[str],
    start: Mapping[str, float],
    *,
    tolerance: float,
    max_iterations: int,
    step: float,
    method: str,
    data_id: str,
    settings: Mapping[str, Setting],
) -> StateSpaceFit:
    """Maximise the likelihood in the logarithms of the drifts' variances, then the noise's.

    A coefficient that does not drift has its variance at zero, stated, never
    estimated.
    """

    size = walks.size
    count = len(walks.values) - size
    starting = _start_values(start, names)

    def model_at(point: Sequence[float]) -> LinearGaussianModel:
        variances = iter([math.exp(x) for x in point])
        drifts = [next(variances) if drifts_too else 0.0 for drifts_too in drifting]
        return walks.model(drifts, next(variances))

    def negative_log_likelihood(point: Sequence[float]) -> float:
        return -walks.filter(model_at(point)).log_likelihood

    def objective(point: Sequence[float]) -> float:
        return negative_log_likelihood(point) / count

    minimum = bfgs(
        objective,
        lambda point: numerical_gradient(objective, point, step=step),
        [math.log(value) for value in starting],
        tolerance=tolerance,
        max_iterations=max_iterations,
    )
    variances = [math.exp(x) for x in minimum.x]
    try:
        logarithmic = curvature_standard_errors(negative_log_likelihood, minimum.x, step)
    except EconometricsInputError as error:
        reached = ", ".join(
            f"{name} {value:.6g}" for name, value in zip(names, variances, strict=True)
        )
        raise EconometricsInputError(
            f"{error} The variances reached are {reached}. One the likelihood is flat in -- a "
            "drift the data do not tell apart from another's, or one driven toward zero, the "
            "region's edge -- cannot be estimated: state that coefficient fixed."
        ) from error
    model = model_at(minimum.x)
    filtered = walks.filter(model)
    stated: dict[str, Setting] = {
        **settings,
        "conditioned": size,
        "max_iterations": max_iterations,
        "observations": len(walks.values),
        "step": float(step),
        "tolerance": float(tolerance),
        **{f"start_{name}": value for name, value in zip(names, starting, strict=True)},
    }
    estimate = Estimate(
        method=method,
        data_id=data_id,
        parameters=dict(zip(names, variances, strict=True)),
        standard_errors={
            name: variance * error
            for name, variance, error in zip(names, variances, logarithmic, strict=True)
        },
        settings=stated,
        numerical_class=NumericalClass.HOST_LIBM,
        convergence=minimum.convergence,
    )
    return StateSpaceFit(
        estimate=estimate,
        log_likelihood=filtered.log_likelihood,
        conditioned=size,
        model=model,
        filtered=filtered,
        smoothed=rts_smoother(model, filtered),
    )


def fit_local_level(
    values: Sequence[float],
    *,
    start: Mapping[str, float],
    tolerance: float,
    max_iterations: int,
    step: float,
) -> StateSpaceFit:
    """Fit the local level, ``y_t = mu_t + v_t`` with ``mu_t = mu_{t-1} + w_t``, by likelihood.

    The level's start is diffuse, conditioned on the first value: the level
    there is ``N(y_1, observation_variance)``, and the likelihood is that of
    the values after it, given it.

    Args:
        values: The series, in time order: 21 values at least.
        start: BFGS's first point: ``level_variance``, the variance of ``w``,
            and ``observation_variance``, of ``v``; each finite and positive.
        tolerance: BFGS's criterion on the gradient of the mean negative
            log-likelihood, in the variances' logarithms.
        max_iterations: BFGS's iterations allowed.
        step: The central differences' step, for the gradient and the
            curvature, in the variances' logarithms.

    Returns:
        The fit: its states are the level at every value.

    Raises:
        EconometricsInputError: If a value is not finite, there are too few, a
            choice is malformed, the start does not name each variance as a
            finite positive number, or the curvature at the maximum is not
            positive definite.
        ConvergenceError: If BFGS does not meet its criterion.
    """

    _choices(tolerance, step, max_iterations)
    series = require_series(values, MINIMUM_STATE_SPACE_OBSERVATIONS + 1, "The series")
    return _fit(
        _random_walks([(1.0,)] * len(series), series, 1.0),
        (True,),
        ("level_variance", "observation_variance"),
        start,
        tolerance=tolerance,
        max_iterations=max_iterations,
        step=step,
        method=LOCAL_LEVEL_METHOD,
        data_id=series_identity(series),
        settings={},
    )


def fit_time_varying_beta(
    response: Sequence[float],
    regressors: Sequence[Sequence[float]],
    *,
    intercept: Intercept,
    start: Mapping[str, float],
    tolerance: float,
    max_iterations: int,
    step: float,
    maximum_condition: float,
) -> StateSpaceFit:
    """Fit ``y_t = a_t + b_t' x_t + v_t``, each coefficient a random walk, by likelihood.

    Each beta drifts by ``w_jt``, with a variance of its own, and so does the
    intercept when it is :attr:`Intercept.DRIFTING`; the drifts are
    independent of each other and of ``v``. With ``k`` coefficients, the
    start is diffuse, conditioned on the first ``k`` observations, whose
    regressors must fix it.

    Beside a regressor far from zero -- a price -- an intercept's drift and a
    beta's are hard to tell apart, and the likelihood may not: an intercept
    whose variance BFGS drives toward zero is refused, and is stated
    :attr:`Intercept.FIXED`.

    Args:
        response: ``y``, in time order.
        regressors: One series or more, each as long as ``response``: ``x``.
        intercept: Whether ``a_t`` is in the model, and whether it drifts.
        start: BFGS's first point, every variance by name, each finite and
            positive: ``intercept_drift_variance`` when the intercept drifts,
            ``beta_1_drift_variance`` to ``beta_p_drift_variance`` for the
            regressors in order, and ``observation_variance``.
        tolerance: BFGS's criterion on the gradient of the mean negative
            log-likelihood, in the variances' logarithms.
        max_iterations: BFGS's iterations allowed.
        step: The central differences' step, in the variances' logarithms.
        maximum_condition: The largest condition number the first ``k``
            observations' regressors may have.

    Returns:
        The fit: its states are the coefficients at every observation from
        the ``k``-th, the intercept first when there is one.

    Raises:
        EconometricsInputError: If a value is not finite, the series differ in
            length, there is no regressor, there are fewer than ``k + 20``
            observations, a choice is malformed, the start does not name each
            variance as a finite positive number, or the curvature at the
            maximum is not positive definite: a variance at zero, its edge.
        IllConditionedError: If the first ``k`` observations' regressors are
            too ill-conditioned to fix the start.
        ConvergenceError: If BFGS does not meet its criterion.
    """

    _choices(tolerance, step, max_iterations)
    if not isinstance(intercept, Intercept):
        raise EconometricsInputError(f"intercept must be an Intercept, got {intercept!r}.")
    if (
        isinstance(maximum_condition, bool)
        or not isinstance(maximum_condition, int | float)
        or not (math.isfinite(maximum_condition) and maximum_condition >= 1.0)
    ):
        raise EconometricsInputError(
            f"maximum_condition is a finite number of 1 at least, got {maximum_condition!r}."
        )
    if not isinstance(regressors, Sequence) or not regressors:
        raise EconometricsInputError("A time-varying beta needs one regressor at least.")
    constant = intercept is not Intercept.NONE
    size = len(regressors) + constant
    ys = require_series(response, size + MINIMUM_STATE_SPACE_OBSERVATIONS, "The response")
    xs = [require_series(each, 1, f"Regressor {i + 1}") for i, each in enumerate(regressors)]
    for i, each in enumerate(xs):
        if len(each) != len(ys):
            raise EconometricsInputError(
                f"Regressor {i + 1} has {len(each)} values and the response {len(ys)}."
            )
    rows = [((1.0,) if constant else ()) + tuple(each[t] for each in xs) for t in range(len(ys))]
    drifting = ((intercept is Intercept.DRIFTING,) if constant else ()) + (True,) * len(xs)
    names = (
        *(("intercept_drift_variance",) if intercept is Intercept.DRIFTING else ()),
        *(f"beta_{i + 1}_drift_variance" for i in range(len(xs))),
        "observation_variance",
    )
    return _fit(
        _random_walks(rows, ys, float(maximum_condition)),
        drifting,
        names,
        start,
        tolerance=tolerance,
        max_iterations=max_iterations,
        step=step,
        method=TIME_VARYING_BETA_METHOD,
        data_id=system_identity([ys, *xs]),
        settings={
            "intercept": intercept.value,
            "maximum_condition": float(maximum_condition),
            "regressors": len(xs),
        },
    )
