"""The Kalman filter, the RTS smoother and the models fitted through them (v4.3, ADR-0074, WS-07).

* **The filter factorises the joint density**: its log-likelihood is the
  observations' joint Gaussian density, for a random walk, a regression whose
  coefficients drift and a two-value observation of a general transition; and
  with nothing drifting its state is Bayesian least squares, held to rational
  arithmetic.
* **The smoother is the posterior**: each smoothed state, the initial one too,
  is the conditional mean and variance given every observation, solved in
  rationals.
* **The diffuse start is a vague prior's limit**: a fit's start is what a prior
  of variance ``10**30`` gives, filtered in rationals, to rounding.
* **Known variances are recovered** within their standard errors, and the
  smoothed beta tracks the true one better than the filtered.
* **What cannot be estimated is refused**: a drift the likelihood is flat in, a
  start the first observations do not fix, malformed models and choices.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from fractions import Fraction

import pytest

from alphalab.common.estimate import NumericalClass
from alphalab.common.linalg import IllConditionedError, cholesky
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import series_identity, system_identity
from alphalab.econometrics.state_space import (
    LOCAL_LEVEL_METHOD,
    MINIMUM_STATE_SPACE_OBSERVATIONS,
    TIME_VARYING_BETA_METHOD,
    Intercept,
    KalmanFilter,
    LinearGaussianModel,
    StateSpaceFit,
    fit_local_level,
    fit_time_varying_beta,
    kalman_filter,
    rts_smoother,
)

TOLERANCE = 1e-6
ITERATIONS = 200
STEP = 1e-5
CHOICES: dict[str, object] = {"tolerance": TOLERANCE, "max_iterations": ITERATIONS, "step": STEP}
CONDITION = 1e8

Matrix = list[list[float]]


def _mul(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> Matrix:
    return [
        [
            math.fsum(x * y for x, y in zip(row, column, strict=True))
            for column in zip(*b, strict=True)
        ]
        for row in a
    ]


def _add(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> Matrix:
    return [[x + y for x, y in zip(r, s, strict=True)] for r, s in zip(a, b, strict=True)]


def _transpose(a: Sequence[Sequence[float]]) -> Matrix:
    return [list(column) for column in zip(*a, strict=True)]


def _joint_log_density(
    model: LinearGaussianModel,
    hs: Sequence[Sequence[Sequence[float]]],
    ys: Sequence[Sequence[float]],
) -> float:
    """``log N(y; mean, Sigma)`` for every observation at once, from the model's moments.

    ``x_t = F^t x_0 + sum_u F^(t-u) w_u``, so ``Cov(x_t, x_s) = F^t P_0 F^s' + sum_u
    F^(t-u) Q F^(s-u)'`` over ``u`` to ``min(t, s)``, and ``y_t = H_t x_t + v_t``.
    """

    size = len(model.initial_mean)
    steps = len(ys)
    powers = [[[1.0 if i == k else 0.0 for k in range(size)] for i in range(size)]]
    for _ in range(steps):
        powers.append(_mul(model.transition, powers[-1]))

    def state_covariance(t: int, s: int) -> Matrix:
        covariance = _mul(_mul(powers[t], model.initial_covariance), _transpose(powers[s]))
        for u in range(1, min(t, s) + 1):
            drift = _mul(
                _mul(powers[t - u], model.transition_covariance), _transpose(powers[s - u])
            )
            covariance = _add(covariance, drift)
        return covariance

    means: list[float] = []
    values: list[float] = []
    for t in range(1, steps + 1):
        state_mean = _mul(powers[t], [[m] for m in model.initial_mean])
        means.extend(row[0] for row in _mul(hs[t - 1], state_mean))
        values.extend(ys[t - 1])
    observed = len(model.observation_covariance)
    sigma = [[0.0] * (steps * observed) for _ in range(steps * observed)]
    for t in range(1, steps + 1):
        for s in range(1, steps + 1):
            block = _mul(_mul(hs[t - 1], state_covariance(t, s)), _transpose(hs[s - 1]))
            if t == s:
                block = _add(block, model.observation_covariance)
            for i in range(observed):
                for k in range(observed):
                    sigma[(t - 1) * observed + i][(s - 1) * observed + k] = block[i][k]
    factor = cholesky(sigma)
    residual = [y - m for y, m in zip(values, means, strict=True)]
    weighted = factor.solve(residual)
    return -0.5 * (
        len(values) * math.log(2.0 * math.pi)
        + math.fsum(math.log(pivot) for pivot in factor.pivots)
        + math.fsum(a * b for a, b in zip(residual, weighted, strict=True))
    )


def _local_level(variance: float, noise: float, mean: float, spread: float) -> LinearGaussianModel:
    return LinearGaussianModel(
        transition=((1.0,),),
        transition_covariance=((variance,),),
        observation_covariance=((noise,),),
        initial_mean=(mean,),
        initial_covariance=((spread,),),
    )


DYADIC = [0.5, -1.25, 0.75, 2.0, 1.5, -0.25, 0.125, 1.0, -0.75, 0.375, 2.5, 1.75]
REGRESSOR = [1.0, 2.5, -0.5, 1.5, 3.0, 0.25, -1.0, 2.0, 0.5, 1.25, -0.75, 1.75]


def _drifting_regression() -> tuple[LinearGaussianModel, list[tuple[tuple[float, float]]]]:
    model = LinearGaussianModel(
        transition=((1.0, 0.0), (0.0, 1.0)),
        transition_covariance=((0.25, 0.0), (0.0, 0.0625)),
        observation_covariance=((0.5,),),
        initial_mean=(1.0, 0.5),
        initial_covariance=((1.0, 0.25), (0.25, 0.5)),
    )
    return model, [((1.0, x),) for x in REGRESSOR]


def _two_values() -> tuple[LinearGaussianModel, list[Sequence[Sequence[float]]], list[list[float]]]:
    model = LinearGaussianModel(
        transition=((0.875, 0.125), (0.0, 0.75)),
        transition_covariance=((0.375, 0.125), (0.125, 0.25)),
        observation_covariance=((0.5, 0.125), (0.125, 0.375)),
        initial_mean=(0.25, -0.5),
        initial_covariance=((2.0, 0.5), (0.5, 1.0)),
    )
    h = ((1.0, 0.0), (0.5, 1.0))
    ys = [[a, b] for a, b in zip(DYADIC, REGRESSOR, strict=True)]
    hs: list[Sequence[Sequence[float]]] = [h] * len(ys)
    return model, hs, ys


def test_the_filters_log_likelihood_is_the_joint_density_of_the_observations() -> None:
    level = _local_level(0.3, 0.7, 0.5, 2.0)
    regression, regression_hs = _drifting_regression()
    general, general_hs, general_ys = _two_values()
    cases: list[
        tuple[LinearGaussianModel, Sequence[Sequence[Sequence[float]]], Sequence[Sequence[float]]]
    ] = [
        (level, [((1.0,),)] * len(DYADIC), [[y] for y in DYADIC]),
        (regression, regression_hs, [[y] for y in DYADIC]),
        (general, general_hs, general_ys),
    ]
    for model, hs, ys in cases:
        filtered = kalman_filter(model, ys, hs)
        assert filtered.log_likelihood == pytest.approx(
            _joint_log_density(model, hs, ys), rel=1e-12
        )
        assert len(filtered.filtered_means) == len(ys)


def _exact_sum(values: Iterable[Fraction]) -> Fraction:
    return sum(values, Fraction(0))


def _solve(matrix: list[list[Fraction]], rhs: list[Fraction]) -> list[Fraction]:
    """Gauss-Jordan elimination, exactly."""

    size = len(matrix)
    rows: list[list[Fraction]] = [[*row, value] for row, value in zip(matrix, rhs, strict=True)]
    for column in range(size):
        pivot = next(r for r in range(column, size) if rows[r][column] != 0)
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for r in range(size):
            if r != column and rows[r][column] != 0:
                ratio = rows[r][column] / rows[column][column]
                rows[r] = [x - ratio * y for x, y in zip(rows[r], rows[column], strict=True)]
    return [rows[i][size] / rows[i][i] for i in range(size)]


def _close(value: float, exact: Fraction, relative: float = 1e-12) -> bool:
    return abs(Fraction(value) - exact) <= Fraction(relative) * max(Fraction(1), abs(exact))


def test_with_nothing_drifting_the_filter_is_bayesian_least_squares_exactly() -> None:
    """``Q = 0``: the last state is ``(P_0⁻¹ + H'H/r)⁻¹ (P_0⁻¹ m_0 + H'y/r)``, in rationals."""

    model = LinearGaussianModel(
        transition=((1.0, 0.0), (0.0, 1.0)),
        transition_covariance=((0.0, 0.0), (0.0, 0.0)),
        observation_covariance=((0.5,),),
        initial_mean=(1.0, 0.5),
        initial_covariance=((1.0, 0.25), (0.25, 0.5)),
    )
    hs = [((1.0, x),) for x in REGRESSOR]
    filtered = kalman_filter(model, [[y] for y in DYADIC], hs)

    p0 = [[Fraction(v) for v in row] for row in model.initial_covariance]
    determinant = p0[0][0] * p0[1][1] - p0[0][1] * p0[1][0]
    precision0 = [
        [p0[1][1] / determinant, -p0[0][1] / determinant],
        [-p0[1][0] / determinant, p0[0][0] / determinant],
    ]
    r = Fraction(model.observation_covariance[0][0])
    precision = [
        [
            precision0[i][k] + _exact_sum(Fraction(h[0][i]) * Fraction(h[0][k]) for h in hs) / r
            for k in range(2)
        ]
        for i in range(2)
    ]
    m0 = [Fraction(v) for v in model.initial_mean]
    information = [
        _exact_sum(precision0[i][k] * m0[k] for k in range(2))
        + _exact_sum(Fraction(h[0][i]) * Fraction(y) for h, y in zip(hs, DYADIC, strict=True)) / r
        for i in range(2)
    ]
    exact_mean = _solve(precision, information)
    exact_covariance = [
        _solve(precision, [Fraction(1 if i == k else 0) for i in range(2)]) for k in range(2)
    ]

    last_mean, last_covariance = filtered.filtered_means[-1], filtered.filtered_covariances[-1]
    assert all(_close(v, e) for v, e in zip(last_mean, exact_mean, strict=True))
    assert all(
        _close(last_covariance[i][k], exact_covariance[k][i]) for i in range(2) for k in range(2)
    )


def _exact_posterior(
    model: LinearGaussianModel, hs: Sequence[Sequence[Sequence[float]]], ys: Sequence[float], t: int
) -> tuple[list[Fraction], list[list[Fraction]]]:
    """``x_t`` given every observation, for a random walk observed one value at a time, exactly."""

    size = len(model.initial_mean)
    m0 = [Fraction(v) for v in model.initial_mean]
    p0 = [[Fraction(v) for v in row] for row in model.initial_covariance]
    q = [[Fraction(v) for v in row] for row in model.transition_covariance]
    r = Fraction(model.observation_covariance[0][0])
    h = [[Fraction(v) for v in matrix[0]] for matrix in hs]

    def state_covariance(a: int, b: int) -> list[list[Fraction]]:
        return [[p0[i][k] + min(a, b) * q[i][k] for k in range(size)] for i in range(size)]

    def times(matrix: list[list[Fraction]], vector: list[Fraction]) -> list[Fraction]:
        return [_exact_sum(x * y for x, y in zip(row, vector, strict=True)) for row in matrix]

    steps = len(ys)
    sigma = [
        [
            _exact_sum(
                x * y
                for x, y in zip(h[a - 1], times(state_covariance(a, b), h[b - 1]), strict=True)
            )
            + (r if a == b else Fraction(0))
            for b in range(1, steps + 1)
        ]
        for a in range(1, steps + 1)
    ]
    cross = [times(state_covariance(t, b), h[b - 1]) for b in range(1, steps + 1)]
    residual = [
        Fraction(y) - _exact_sum(x * m for x, m in zip(h[b], m0, strict=True))
        for b, y in enumerate(ys)
    ]
    weights = _solve(sigma, residual)
    mean = [m0[i] + _exact_sum(cross[b][i] * weights[b] for b in range(steps)) for i in range(size)]
    covariance = []
    for i in range(size):
        solved = _solve(sigma, [cross[b][i] for b in range(steps)])
        covariance.append(
            [
                state_covariance(t, t)[i][k]
                - _exact_sum(cross[b][k] * solved[b] for b in range(steps))
                for k in range(size)
            ]
        )
    return mean, covariance


@pytest.mark.parametrize("kind", ["local level", "drifting regression"])
def test_each_smoothed_state_is_its_posterior_given_every_observation(kind: str) -> None:
    hs: Sequence[Sequence[Sequence[float]]]
    if kind == "local level":
        model, hs = _local_level(0.25, 0.75, 0.5, 2.0), [((1.0,),)] * 8
    else:
        model, all_hs = _drifting_regression()
        hs = all_hs[:8]
    ys = DYADIC[:8]
    filtered = kalman_filter(model, [[y] for y in ys], hs)
    smoothed = rts_smoother(model, filtered)

    states = [
        (smoothed.initial_mean, smoothed.initial_covariance),
        *zip(smoothed.smoothed_means, smoothed.smoothed_covariances, strict=True),
    ]
    assert len(states) == len(ys) + 1
    for t, (mean, covariance) in enumerate(states):
        exact_mean, exact_covariance = _exact_posterior(model, hs, ys, t)
        assert all(_close(v, e, 1e-11) for v, e in zip(mean, exact_mean, strict=True)), t
        assert all(
            _close(covariance[i][k], exact_covariance[i][k], 1e-11)
            for i in range(len(mean))
            for k in range(len(mean))
        ), t
    assert smoothed.smoothed_means[-1] == filtered.filtered_means[-1]


def _vague_start(
    rows: Sequence[Sequence[float]], ys: Sequence[float], drifts: Sequence[float], noise: float
) -> tuple[list[Fraction], list[list[Fraction]]]:
    """The state at observation ``k`` given the first ``k``, from ``N(0, 10**30 I)``, exactly."""

    size = len(rows[0])
    vague = Fraction(10) ** 30
    mean = [Fraction(0)] * size
    covariance = [[vague if i == k else Fraction(0) for k in range(size)] for i in range(size)]
    r = Fraction(noise)
    for row, y in zip(rows[:size], ys[:size], strict=True):
        h = [Fraction(v) for v in row]
        prior = [
            [covariance[i][k] + (Fraction(drifts[i]) if i == k else 0) for k in range(size)]
            for i in range(size)
        ]
        cross = [_exact_sum(prior[i][k] * h[k] for k in range(size)) for i in range(size)]
        s = _exact_sum(h[i] * cross[i] for i in range(size)) + r
        gain = [c / s for c in cross]
        innovation = Fraction(y) - _exact_sum(h[i] * mean[i] for i in range(size))
        mean = [m + g * innovation for m, g in zip(mean, gain, strict=True)]
        covariance = [
            [prior[i][k] - gain[i] * gain[k] * s for k in range(size)] for i in range(size)
        ]
    return mean, covariance


def _price_pair(seed: int, count: int) -> tuple[list[float], list[float], list[float]]:
    """``y = 1 + b_t x_t + v``: ``x`` a price walking near 50, ``b_t`` a hedge ratio drifting."""

    draws = random.Random(seed)
    beta, price, xs, ys, betas = 0.8, 50.0, [], [], []
    for _ in range(count):
        beta += draws.gauss(0.0, 0.01)
        price += draws.gauss(0.0, 1.0)
        xs.append(price)
        ys.append(1.0 + beta * price + draws.gauss(0.0, 0.5))
        betas.append(beta)
    return ys, xs, betas


PAIR_START = {"beta_1_drift_variance": 1e-3, "observation_variance": 0.5}


def test_a_fits_diffuse_start_is_the_limit_of_an_ever_vaguer_prior() -> None:
    ys, xs, _ = _price_pair(9, 120)
    fit = fit_time_varying_beta(
        ys,
        [xs],
        intercept=Intercept.FIXED,
        start=PAIR_START,
        maximum_condition=CONDITION,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )
    drifts = [fit.model.transition_covariance[i][i] for i in range(2)]
    noise = fit.model.observation_covariance[0][0]
    assert drifts[0] == 0.0
    exact_mean, exact_covariance = _vague_start([(1.0, x) for x in xs], ys, drifts, noise)

    assert all(_close(v, e, 1e-10) for v, e in zip(fit.model.initial_mean, exact_mean, strict=True))
    assert all(
        _close(fit.model.initial_covariance[i][k], exact_covariance[i][k], 1e-10)
        for i in range(2)
        for k in range(2)
    )


def test_a_local_levels_start_is_its_first_value_with_the_noise_as_its_variance() -> None:
    draws = random.Random(3)
    level, values = 0.0, []
    for _ in range(200):
        level += draws.gauss(0.0, 0.3)
        values.append(level + draws.gauss(0.0, 1.0))
    fit = fit_local_level(
        values,
        start={"level_variance": 0.1, "observation_variance": 1.0},
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )

    assert fit.model.initial_mean == (values[0],)
    assert fit.model.initial_covariance == ((fit.estimate.parameters["observation_variance"],),)
    assert fit.conditioned == 1
    assert len(fit.filtered_states) == len(fit.smoothed_states) == len(values)
    assert fit.filtered_states[0] == (values[0],)


def test_a_fit_reports_the_filter_and_smoother_at_its_estimate() -> None:
    ys, xs, _ = _price_pair(5, 150)
    fit = fit_time_varying_beta(
        ys,
        [xs],
        intercept=Intercept.FIXED,
        start=PAIR_START,
        maximum_condition=CONDITION,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )
    k = fit.conditioned
    filtered = kalman_filter(fit.model, [[y] for y in ys[k:]], [((1.0, x),) for x in xs[k:]])

    assert k == 2
    assert filtered == fit.filtered
    assert rts_smoother(fit.model, filtered) == fit.smoothed
    assert fit.log_likelihood == filtered.log_likelihood
    assert len(fit.filtered_states) == len(fit.smoothed_states) == len(ys) - k + 1


def _local_level_series(seed: int, count: int) -> list[float]:
    draws = random.Random(seed)
    level, values = 0.0, []
    for _ in range(count):
        level += draws.gauss(0.0, math.sqrt(0.1))
        values.append(level + draws.gauss(0.0, 1.0))
    return values


def _market_beta(seed: int, count: int) -> tuple[list[float], list[float]]:
    """``y = a_t + b_t x_t + v``: returns ``x`` about zero, both coefficients drifting."""

    draws = random.Random(seed)
    alpha, beta, xs, ys = 0.0, 1.0, [], []
    for _ in range(count):
        alpha += draws.gauss(0.0, 0.02)
        beta += draws.gauss(0.0, 0.03)
        x = draws.gauss(0.0, 1.0)
        xs.append(x)
        ys.append(alpha + beta * x + draws.gauss(0.0, 0.3))
    return ys, xs


def _recover(case: str) -> tuple[StateSpaceFit, dict[str, float]]:
    if case == "local level":
        fit = fit_local_level(
            _local_level_series(7, 600),
            start={"level_variance": 0.5, "observation_variance": 0.5},
            tolerance=TOLERANCE,
            max_iterations=ITERATIONS,
            step=STEP,
        )
        return fit, {"level_variance": 0.1, "observation_variance": 1.0}
    if case == "hedge ratio":
        ys, xs, _ = _price_pair(9, 500)
        fit = fit_time_varying_beta(
            ys,
            [xs],
            intercept=Intercept.FIXED,
            start=PAIR_START,
            maximum_condition=CONDITION,
            tolerance=TOLERANCE,
            max_iterations=ITERATIONS,
            step=STEP,
        )
        return fit, {"beta_1_drift_variance": 1e-4, "observation_variance": 0.25}
    ys, xs = _market_beta(11, 800)
    fit = fit_time_varying_beta(
        ys,
        [xs],
        intercept=Intercept.DRIFTING,
        start={
            "intercept_drift_variance": 1e-3,
            "beta_1_drift_variance": 1e-3,
            "observation_variance": 0.5,
        },
        maximum_condition=CONDITION,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )
    return fit, {
        "intercept_drift_variance": 0.02**2,
        "beta_1_drift_variance": 0.03**2,
        "observation_variance": 0.09,
    }


@pytest.mark.parametrize("case", ["local level", "hedge ratio", "market beta"])
def test_known_variances_are_recovered_within_their_standard_errors(case: str) -> None:
    fit, truth = _recover(case)

    assert sorted(fit.estimate.parameters) == sorted(truth)
    for name, value in truth.items():
        estimate = fit.estimate.parameters[name]
        error = fit.estimate.standard_errors[name]
        assert 0.0 < error < estimate
        assert abs(estimate - value) < 3.0 * error, (name, estimate, value, error)


def test_the_smoothed_hedge_ratio_tracks_the_true_one_better_than_the_filtered() -> None:
    ys, xs, betas = _price_pair(9, 500)
    fit = fit_time_varying_beta(
        ys,
        [xs],
        intercept=Intercept.FIXED,
        start=PAIR_START,
        maximum_condition=CONDITION,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )
    truth = betas[fit.conditioned - 1 :]

    def error(states: Sequence[Sequence[float]]) -> float:
        return math.fsum((s[1] - b) ** 2 for s, b in zip(states, truth, strict=True)) / len(truth)

    smoothed, filtered = error(fit.smoothed_states), error(fit.filtered_states)
    assert smoothed < filtered
    assert math.sqrt(smoothed) < 0.02
    # The fixed intercept, smoothed, is one number throughout, to rounding.
    intercepts = [state[0] for state in fit.smoothed_states]
    assert max(intercepts) - min(intercepts) < 1e-9


def test_the_estimate_states_every_choice_and_its_convergence() -> None:
    values = _local_level_series(2, 120)
    level = fit_local_level(
        values,
        start={"level_variance": 0.5, "observation_variance": 0.5},
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )
    ys, xs, _ = _price_pair(4, 120)
    beta = fit_time_varying_beta(
        ys,
        [xs],
        intercept=Intercept.FIXED,
        start=PAIR_START,
        maximum_condition=CONDITION,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )

    assert level.estimate.method == LOCAL_LEVEL_METHOD
    assert level.estimate.data_id == series_identity(values)
    assert beta.estimate.method == TIME_VARYING_BETA_METHOD
    assert beta.estimate.data_id == system_identity([ys, xs])
    for fit in (level, beta):
        estimate = fit.estimate
        assert estimate.numerical_class is NumericalClass.HOST_LIBM
        assert estimate.convergence is not None and estimate.convergence.method == "BFGS"
        assert estimate.convergence.reached <= TOLERANCE
    assert dict(level.estimate.settings) == {
        "conditioned": 1,
        "max_iterations": 200,
        "observations": 120,
        "start_level_variance": 0.5,
        "start_observation_variance": 0.5,
        "step": 1e-5,
        "tolerance": 1e-6,
    }
    settings = dict(beta.estimate.settings)
    assert settings["intercept"] == "fixed" and settings["regressors"] == 1
    assert settings["maximum_condition"] == CONDITION and settings["conditioned"] == 2
    assert settings["start_beta_1_drift_variance"] == 1e-3


def test_a_drift_the_likelihood_is_flat_in_is_refused() -> None:
    """The regressor is zero after the start: its beta's drift moves no innovation, bit for bit."""

    values = _local_level_series(6, 120)
    regressor = [0.0, 1.0] + [0.0] * 118
    with pytest.raises(EconometricsInputError, match="state that coefficient fixed"):
        fit_time_varying_beta(
            values,
            [regressor],
            intercept=Intercept.DRIFTING,
            start={
                "intercept_drift_variance": 0.1,
                "beta_1_drift_variance": 0.1,
                "observation_variance": 1.0,
            },
            maximum_condition=CONDITION,
            tolerance=TOLERANCE,
            max_iterations=ITERATIONS,
            step=STEP,
        )


def test_a_start_the_first_observations_do_not_fix_is_refused() -> None:
    xs = [10.0, 10.0] + [10.0 + i / 8 for i in range(40)]
    with pytest.raises(IllConditionedError, match="first 2 observations fix the start"):
        fit_time_varying_beta(
            [2.0 * x for x in xs],
            [xs],
            intercept=Intercept.FIXED,
            start=PAIR_START,
            maximum_condition=CONDITION,
            tolerance=TOLERANCE,
            max_iterations=ITERATIONS,
            step=STEP,
        )


LEVEL_START = {"level_variance": 0.5, "observation_variance": 0.5}
SERIES = [float(i % 7) for i in range(40)]


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"values": [*SERIES[:20]]}, f"needs at least {MINIMUM_STATE_SPACE_OBSERVATIONS + 1}"),
        ({"values": [*SERIES[:-1], math.nan]}, "not finite"),
        ({"start": {"level_variance": 0.5}}, "names each variance"),
        ({"start": {"level_variance": 0.0, "observation_variance": 0.5}}, "finite positive"),
        ({"start": {"level_variance": True, "observation_variance": 0.5}}, "finite positive"),
        ({"tolerance": 0.0}, "tolerance"),
        ({"step": math.nan}, "step"),
        ({"max_iterations": 0}, "max_iterations"),
        ({"max_iterations": True}, "max_iterations"),
    ],
)
def test_what_a_local_level_cannot_take_is_refused(
    arguments: dict[str, object], match: str
) -> None:
    call: dict[str, object] = {"values": SERIES, "start": LEVEL_START, **CHOICES, **arguments}
    with pytest.raises(EconometricsInputError, match=match):
        fit_local_level(**call)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"intercept": True}, "must be an Intercept"),
        ({"regressors": []}, "one regressor at least"),
        ({"regressors": [SERIES[:-1]]}, "Regressor 1 has 39 values"),
        ({"maximum_condition": 0.5}, "maximum_condition"),
        ({"response": SERIES[:21]}, f"needs at least {MINIMUM_STATE_SPACE_OBSERVATIONS + 2}"),
        ({"start": {"observation_variance": 0.5}}, "beta_1_drift_variance"),
        (
            {"intercept": Intercept.DRIFTING, "start": PAIR_START},
            "intercept_drift_variance",
        ),
    ],
)
def test_what_a_time_varying_beta_cannot_take_is_refused(
    arguments: dict[str, object], match: str
) -> None:
    call: dict[str, object] = {
        "response": SERIES,
        "regressors": [[float(i) for i in range(40)]],
        "intercept": Intercept.FIXED,
        "start": PAIR_START,
        "maximum_condition": CONDITION,
        **CHOICES,
        **arguments,
    }
    with pytest.raises(EconometricsInputError, match=match):
        fit_time_varying_beta(**call)  # type: ignore[arg-type]


def _model(**changes: object) -> LinearGaussianModel:
    fields: dict[str, object] = {
        "transition": ((1.0, 0.0), (0.0, 1.0)),
        "transition_covariance": ((0.1, 0.0), (0.0, 0.1)),
        "observation_covariance": ((1.0,),),
        "initial_mean": (0.0, 0.0),
        "initial_covariance": ((1.0, 0.0), (0.0, 1.0)),
        **changes,
    }
    return LinearGaussianModel(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"initial_mean": ()}, "one at least"),
        ({"observation_covariance": ()}, "one at least"),
        ({"transition": ((1.0,),)}, "F is 2 x 2"),
        ({"transition_covariance": ((0.1, 0.2), (0.0, 0.1))}, "Q is a covariance"),
        ({"transition_covariance": ((-0.1, 0.0), (0.0, 0.1))}, "negative variance"),
        ({"initial_covariance": ((1.0, 0.0), (0.0, math.inf))}, "not finite"),
        ({"initial_mean": (0.0, "1")}, "not a number"),
        ({"observation_covariance": ((0.0,),)}, "positive definite"),
    ],
)
def test_a_malformed_model_is_refused(changes: dict[str, object], match: str) -> None:
    with pytest.raises(EconometricsInputError, match=match):
        _model(**changes)


def test_what_the_filter_and_smoother_cannot_take_is_refused() -> None:
    model = _model()
    h = ((1.0, 0.5),)
    with pytest.raises(EconometricsInputError, match="one observation at least"):
        kalman_filter(model, [], [])
    with pytest.raises(EconometricsInputError, match="as many matrices"):
        kalman_filter(model, [[1.0], [2.0]], [h])
    with pytest.raises(EconometricsInputError, match="H at observation 1 is 1 x 2"):
        kalman_filter(model, [[1.0]], [((1.0,),)])
    with pytest.raises(EconometricsInputError, match="Observation 2 is 1 x 1"):
        kalman_filter(model, [[1.0], [1.0, 2.0]], [h, h])
    with pytest.raises(EconometricsInputError, match="must be a LinearGaussianModel"):
        kalman_filter("model", [[1.0]], [h])  # type: ignore[arg-type]
    indefinite = _model(
        initial_covariance=((1.0, 2.0), (2.0, 1.0)), transition_covariance=((0.0, 0.0), (0.0, 0.0))
    )
    with pytest.raises(EconometricsInputError, match="innovation's covariance at observation 1"):
        kalman_filter(indefinite, [[1.0]], [((1.0, -1.0),)])
    certain = _model(
        initial_covariance=((0.0, 0.0), (0.0, 0.0)),
        transition_covariance=((0.0, 0.0), (0.0, 0.0)),
    )
    filtered = kalman_filter(certain, [[1.0], [2.0]], [h, h])
    with pytest.raises(EconometricsInputError, match="no uncertainty to smooth back through"):
        rts_smoother(certain, filtered)
    level = _local_level(0.1, 1.0, 0.0, 1.0)
    with pytest.raises(EconometricsInputError, match="another model's"):
        rts_smoother(level, kalman_filter(model, [[1.0]], [h]))
    with pytest.raises(EconometricsInputError, match="must be a KalmanFilter"):
        rts_smoother(model, "filtered")  # type: ignore[arg-type]
    assert isinstance(kalman_filter(model, [[1.0]], [h]), KalmanFilter)
