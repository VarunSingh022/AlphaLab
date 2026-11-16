"""Hidden Markov and Markov-switching estimation (v4.3, ADR-0075, WS-07).

* **Hand-computed references** (ADR-0075 section 7): at a fit's parameters,
  the forward-backward probabilities, the likelihood and the most likely path
  are recomputed in exact fractions from the densities, and agree.
* **Simulated regime recovery**: chains of two and three states, a
  Markov-switching regression and a switching autoregression recover their
  parameters within three standard errors, and the most likely path agrees
  with the true states at a stated share of instants.
* **One labelling**: a start with its states listed the other way round
  reaches the same estimate, the same states and the same path.
* **A structural zero is kept**, with no standard error, and the rest of its
  row is estimated.
* **What is refused**: a collapsing state, named; a fit short of its
  criterion; a transition the data do not show; and every malformed choice.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from fractions import Fraction

import pytest

from alphalab.common.estimate import NumericalClass
from alphalab.common.exceptions import ConvergenceError
from alphalab.econometrics.exceptions import CollapsedStateError, EconometricsInputError
from alphalab.econometrics.regimes import (
    GAUSSIAN_HMM_METHOD,
    MARKOV_SWITCHING_METHOD,
    MINIMUM_REGIME_OBSERVATIONS,
    QuantileStart,
    RegimeFit,
    _Parameters,
    _viterbi,
    fit_gaussian_hmm,
    fit_markov_switching_regression,
)
from alphalab.econometrics.series import series_identity, system_identity

TOLERANCE = 1e-8
ITERATIONS = 500
STEP = 1e-5
CONDITION = 1e8
START = QuantileStart(0.9)


def _chain(draws: random.Random, transition: Sequence[Sequence[float]], count: int) -> list[int]:
    state, states = 0, []
    for _ in range(count):
        u, total = draws.random(), 0.0
        for k, p in enumerate(transition[state]):
            total += p
            if u < total:
                state = k
                break
        states.append(state)
    return states


def _hmm_series(
    seed: int,
    count: int,
    transition: Sequence[Sequence[float]],
    means: Sequence[float],
    deviations: Sequence[float],
) -> tuple[list[float], list[int]]:
    draws = random.Random(seed)
    states = _chain(draws, transition, count)
    return [draws.gauss(means[s], deviations[s]) for s in states], states


def _hmm(values: Sequence[float], states: int = 2, start: object = START) -> RegimeFit:
    return fit_gaussian_hmm(
        values,
        states=states,
        start=start,  # type: ignore[arg-type]
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )


def _density(value: float, mean: float, variance: float) -> Fraction:
    return Fraction(
        math.exp(
            -0.5 * (math.log(2.0 * math.pi) + math.log(variance) + (value - mean) ** 2 / variance)
        )
    )


def _exact(
    values: Sequence[float], parameters: Mapping[str, float], states: int
) -> tuple[list[list[Fraction]], list[list[Fraction]], Fraction, tuple[int, ...]]:
    """Forward, backward and Viterbi in exact fractions, from each instant's float density."""

    ks = range(states)
    a = [[Fraction(parameters[f"transition_{j + 1}_{k + 1}"]) for k in ks] for j in ks]
    initial = [Fraction(parameters[f"initial_{k + 1}"]) for k in ks]
    densities = [
        [
            _density(y, parameters[f"state_{k + 1}_mean"], parameters[f"state_{k + 1}_variance"])
            for k in ks
        ]
        for y in values
    ]
    alpha = [[initial[k] * densities[0][k] for k in ks]]
    for d in densities[1:]:
        alpha.append([sum((alpha[-1][j] * a[j][k] for j in ks), Fraction(0)) * d[k] for k in ks])
    likelihood = sum(alpha[-1], Fraction(0))
    beta = [[Fraction(1)] * states]
    for d in reversed(densities[1:]):
        beta.insert(0, [sum((a[j][k] * d[k] * beta[0][k] for k in ks), Fraction(0)) for j in ks])
    filtered = [[x / sum(row, Fraction(0)) for x in row] for row in alpha]
    smoothed = [
        [x * b / likelihood for x, b in zip(row, bs, strict=True)]
        for row, bs in zip(alpha, beta, strict=True)
    ]
    scores = [initial[k] * densities[0][k] for k in ks]
    pointers = []
    for d in densities[1:]:
        best: list[int] = []
        for k in ks:
            # The largest product, a tie to the lower state, as the float recursion breaks it.
            products = [scores[j] * a[j][k] for j in ks]
            chosen = 0
            for j in ks[1:]:
                if products[j] > products[chosen]:
                    chosen = j
            best.append(chosen)
        scores = [scores[best[k]] * a[best[k]][k] * d[k] for k in ks]
        pointers.append(best)
    state = max(ks, key=lambda k: (scores[k], -k))
    path = [state]
    for best in reversed(pointers):
        state = best[state]
        path.append(state)
    return filtered, smoothed, likelihood, tuple(s + 1 for s in reversed(path))


def test_the_probabilities_likelihood_and_path_agree_with_exact_fractions() -> None:
    values, _ = _hmm_series(4, 48, [[0.9, 0.1], [0.2, 0.8]], [0.0, 2.0], [0.6, 1.0])
    fit = _hmm(values)
    filtered, smoothed, likelihood, path = _exact(values, fit.estimate.parameters, 2)

    for got, exact in ((fit.filtered, filtered), (fit.smoothed, smoothed)):
        assert len(got) == len(values)
        for row, exact_row in zip(got, exact, strict=True):
            assert all(
                abs(Fraction(v) - e) <= Fraction(1, 10**12)
                for v, e in zip(row, exact_row, strict=True)
            )
    log_likelihood = math.log(likelihood.numerator) - math.log(likelihood.denominator)
    assert fit.log_likelihood == pytest.approx(log_likelihood, rel=1e-12)
    assert fit.path == path


def _within(fit: RegimeFit, truth: Mapping[str, float], errors: float = 3.0) -> None:
    for name, value in truth.items():
        estimate = fit.estimate.parameters[name]
        error = fit.estimate.standard_errors[name]
        assert error > 0.0, name
        assert abs(estimate - value) < errors * error, (name, estimate, value, error)


def _agreement(path: Sequence[int], states: Sequence[int], labels: Sequence[int]) -> float:
    return sum(p == labels[s] for p, s in zip(path, states, strict=True)) / len(states)


def test_a_two_state_chain_is_recovered_and_its_path_found() -> None:
    transition = [[0.97, 0.03], [0.05, 0.95]]
    values, states = _hmm_series(3, 1000, transition, [0.5, -1.0], [0.5, 2.0])
    fit = _hmm(values)

    # Ascending means: the true state 1 (mean -1) is the estimate's state 1.
    _within(
        fit,
        {
            "state_1_mean": -1.0,
            "state_1_variance": 4.0,
            "state_2_mean": 0.5,
            "state_2_variance": 0.25,
            "transition_1_1": 0.95,
            "transition_1_2": 0.05,
            "transition_2_1": 0.03,
            "transition_2_2": 0.97,
        },
    )
    assert _agreement(fit.path, states, [2, 1]) >= 0.95
    assert all(math.fsum(row) == pytest.approx(1.0, abs=1e-12) for row in fit.smoothed)


def test_a_three_state_chain_is_recovered_and_its_path_found() -> None:
    transition = [[0.9, 0.07, 0.03], [0.05, 0.9, 0.05], [0.03, 0.07, 0.9]]
    values, states = _hmm_series(8, 1500, transition, [-2.0, 0.0, 2.0], [0.5, 0.7, 0.6])
    fit = _hmm(values, states=3)

    truth = {f"transition_{j + 1}_{k + 1}": transition[j][k] for j in range(3) for k in range(3)}
    truth.update(
        {
            "state_1_mean": -2.0,
            "state_2_mean": 0.0,
            "state_3_mean": 2.0,
            "state_1_variance": 0.25,
            "state_2_variance": 0.49,
            "state_3_variance": 0.36,
        }
    )
    _within(fit, truth)
    assert _agreement(fit.path, states, [1, 2, 3]) >= 0.95


def _switching(seed: int, count: int) -> tuple[list[float], list[float], list[int]]:
    draws = random.Random(seed)
    states = _chain(draws, [[0.95, 0.05], [0.1, 0.9]], count)
    xs, ys = [], []
    for s in states:
        x = draws.gauss(0.0, 1.0)
        a, b, deviation = ((0.0, 1.0, 0.5), (1.0, -0.5, 1.5))[s]
        xs.append(x)
        ys.append(a + b * x + draws.gauss(0.0, deviation))
    return ys, xs, states


def _regression(
    ys: Sequence[float],
    regressors: Sequence[Sequence[float]],
    *,
    intercept: bool = True,
    start: object = START,
) -> RegimeFit:
    return fit_markov_switching_regression(
        ys,
        regressors,
        intercept=intercept,
        states=2,
        start=start,  # type: ignore[arg-type]
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
        maximum_condition=CONDITION,
    )


def test_a_markov_switching_regression_is_recovered() -> None:
    ys, xs, states = _switching(5, 1200)
    fit = _regression(ys, [xs])

    # Ascending variance: the calm state is state 1.
    _within(
        fit,
        {
            "state_1_intercept": 0.0,
            "state_1_beta_1": 1.0,
            "state_1_variance": 0.25,
            "state_2_intercept": 1.0,
            "state_2_beta_1": -0.5,
            "state_2_variance": 2.25,
            "transition_1_1": 0.95,
            "transition_2_2": 0.9,
        },
    )
    assert _agreement(fit.path, states, [1, 2]) >= 0.9


def test_a_switching_autoregression_takes_its_lags_as_regressors() -> None:
    draws = random.Random(12)
    states = _chain(draws, [[0.95, 0.05], [0.08, 0.92]], 900)
    y = [0.0]
    for s in states:
        constant, phi, deviation = ((0.5, 0.3, 0.4), (-0.5, 0.8, 1.2))[s]
        y.append(constant + phi * y[-1] + draws.gauss(0.0, deviation))
    fit = _regression(y[1:], [y[:-1]])

    _within(
        fit,
        {
            "state_1_intercept": 0.5,
            "state_1_beta_1": 0.3,
            "state_1_variance": 0.16,
            "state_2_intercept": -0.5,
            "state_2_beta_1": 0.8,
            "state_2_variance": 1.44,
        },
    )
    assert _agreement(fit.path, states, [1, 2]) >= 0.9


def test_a_start_listing_its_states_the_other_way_reaches_the_same_states() -> None:
    values, _ = _hmm_series(6, 400, [[0.95, 0.05], [0.1, 0.9]], [0.0, 3.0], [1.0, 1.0])
    forward = {
        "state_1_mean": -0.5,
        "state_1_variance": 1.5,
        "state_2_mean": 2.5,
        "state_2_variance": 1.5,
        "transition_1_1": 0.9,
        "transition_1_2": 0.1,
        "transition_2_1": 0.2,
        "transition_2_2": 0.8,
        "initial_1": 0.5,
        "initial_2": 0.5,
    }
    reverse = {
        "state_1_mean": 2.5,
        "state_1_variance": 1.5,
        "state_2_mean": -0.5,
        "state_2_variance": 1.5,
        "transition_1_1": 0.8,
        "transition_1_2": 0.2,
        "transition_2_1": 0.1,
        "transition_2_2": 0.9,
        "initial_1": 0.5,
        "initial_2": 0.5,
    }
    one, other = _hmm(values, start=forward), _hmm(values, start=reverse)

    assert one.estimate.parameters["state_1_mean"] < one.estimate.parameters["state_2_mean"]
    assert one.estimate.parameters == pytest.approx(
        dict(other.estimate.parameters), rel=1e-5, abs=1e-9
    )
    assert one.path == other.path
    assert one.estimate.estimate_id != other.estimate.estimate_id  # the starts differ


def test_a_structural_zero_is_kept_with_no_standard_error() -> None:
    draws = random.Random(2)
    values = [draws.gauss(0.0, 1.0) for _ in range(150)] + [
        draws.gauss(3.0, 1.0) for _ in range(150)
    ]
    start = {
        "state_1_mean": 0.5,
        "state_1_variance": 1.0,
        "state_2_mean": 2.5,
        "state_2_variance": 1.0,
        "transition_1_1": 0.99,
        "transition_1_2": 0.01,
        "transition_2_1": 0.0,
        "transition_2_2": 1.0,
        "initial_1": 1.0,
        "initial_2": 0.0,
    }
    fit = _hmm(values, start=start)
    parameters, errors = fit.estimate.parameters, fit.estimate.standard_errors

    assert parameters["transition_2_1"] == 0.0 and parameters["transition_2_2"] == 1.0
    assert "transition_2_1" not in errors and "transition_2_2" not in errors
    assert "initial_1" not in errors
    assert errors["transition_1_2"] > 0.0
    assert parameters["transition_1_2"] == pytest.approx(1 / 150, rel=0.2)
    assert fit.path == (1,) * 150 + (2,) * 150


def test_a_transition_the_data_do_not_show_is_refused_as_a_structural_zero() -> None:
    draws = random.Random(2)
    values = [draws.gauss(0.0, 1.0) for _ in range(150)] + [
        draws.gauss(3.0, 1.0) for _ in range(150)
    ]
    with pytest.raises(EconometricsInputError, match=r"transition_2_1.*structural zero"):
        _hmm(values)


def test_a_collapsing_state_is_refused_and_named() -> None:
    draws = random.Random(1)
    values = [0.0] * 40 + [draws.gauss(0.0, 1.0) for _ in range(60)]
    draws.shuffle(values)
    with pytest.raises(CollapsedStateError, match=r"has a variance of 0\.0") as caught:
        _hmm(values)
    assert isinstance(caught.value, ConvergenceError)
    assert caught.value.state in (1, 2) and caught.value.variance == 0.0
    assert caught.value.reached == math.inf


def test_a_fit_short_of_its_criterion_raises_with_the_likelihood_reached() -> None:
    values, _ = _hmm_series(3, 200, [[0.9, 0.1], [0.1, 0.9]], [0.0, 2.0], [1.0, 1.0])
    with pytest.raises(ConvergenceError, match="the log-likelihood at -"):
        fit_gaussian_hmm(
            values, states=2, start=START, tolerance=1e-12, max_iterations=2, step=STEP
        )


def test_the_estimate_states_every_choice_and_its_convergence() -> None:
    values, _ = _hmm_series(3, 200, [[0.9, 0.1], [0.1, 0.9]], [0.0, 3.0], [1.0, 1.0])
    hmm = _hmm(values)
    ys, xs, _ = _switching(5, 200)
    regression = _regression(ys, [xs])

    assert hmm.estimate.method == GAUSSIAN_HMM_METHOD
    assert hmm.estimate.data_id == series_identity(values)
    assert regression.estimate.method == MARKOV_SWITCHING_METHOD
    assert regression.estimate.data_id == system_identity([ys, xs])
    for fit in (hmm, regression):
        assert fit.states == 2
        assert fit.estimate.numerical_class is NumericalClass.HOST_LIBM
        report = fit.estimate.convergence
        assert (
            report is not None and report.criterion == "the absolute change of the log-likelihood"
        )
        assert report.reached <= TOLERANCE
    assert dict(hmm.estimate.settings) == {
        "max_iterations": ITERATIONS,
        "observations": 200,
        "start": "quantile",
        "start_persistence": 0.9,
        "states": 2,
        "step": STEP,
        "tolerance": TOLERANCE,
    }
    settings = dict(regression.estimate.settings)
    assert settings["intercept"] is True and settings["regressors"] == 1
    assert settings["maximum_condition"] == CONDITION


SERIES, _ = _hmm_series(9, 60, [[0.9, 0.1], [0.1, 0.9]], [0.0, 3.0], [1.0, 1.0])
EXPLICIT = {
    "state_1_mean": 0.0,
    "state_1_variance": 1.0,
    "state_2_mean": 3.0,
    "state_2_variance": 1.0,
    "transition_1_1": 0.9,
    "transition_1_2": 0.1,
    "transition_2_1": 0.1,
    "transition_2_2": 0.9,
    "initial_1": 0.5,
    "initial_2": 0.5,
}


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"states": 1}, "two at least"),
        ({"states": True}, "two at least"),
        ({"tolerance": 0.0}, "tolerance"),
        ({"step": math.inf}, "step"),
        ({"max_iterations": 0}, "max_iterations"),
        ({"values": SERIES[: 2 * MINIMUM_REGIME_OBSERVATIONS - 1]}, "needs at least 40"),
        ({"values": [1.0] * 60}, "does not vary"),
        ({"values": [*SERIES[:-1], math.nan]}, "not finite"),
        ({"start": "quantile"}, "QuantileStart or a mapping"),
        ({"start": {**EXPLICIT, "initial_3": 0.0}}, "names every parameter"),
        ({"start": {**EXPLICIT, "state_2_variance": 0.0}}, "a variance is positive"),
        ({"start": {**EXPLICIT, "transition_1_1": 0.8}}, "transition row 1 is a distribution"),
        ({"start": {**EXPLICIT, "initial_1": 1.5, "initial_2": -0.5}}, "initial distribution"),
        ({"start": {**EXPLICIT, "state_1_mean": math.nan}}, "not a finite number"),
        ({"start": {**EXPLICIT, "state_2_mean": 0.0}}, "starts as another state does"),
        ({"values": [0.0] * 50 + [1.0] * 10}, "starts as another state does"),
    ],
)
def test_what_a_hidden_markov_model_cannot_take_is_refused(
    arguments: dict[str, object], match: str
) -> None:
    call: dict[str, object] = {
        "values": SERIES,
        "states": 2,
        "start": START,
        "tolerance": TOLERANCE,
        "max_iterations": ITERATIONS,
        "step": STEP,
        **arguments,
    }
    with pytest.raises(EconometricsInputError, match=match):
        fit_gaussian_hmm(**call)  # type: ignore[arg-type]


@pytest.mark.parametrize("persistence", [0.0, 1.0, math.nan, True, "0.9"])
def test_a_quantile_start_is_a_persistence_in_the_open_unit_interval(persistence: object) -> None:
    with pytest.raises(EconometricsInputError, match="persistence"):
        QuantileStart(persistence)  # type: ignore[arg-type]


RESPONSE, REGRESSOR, _ = _switching(5, 120)


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"intercept": 1}, "intercept is a bool"),
        ({"regressors": []}, "fit_gaussian_hmm's"),
        ({"regressors": [REGRESSOR[:-1]]}, "Regressor 1 has 119 values"),
        ({"maximum_condition": 0.5}, "maximum_condition"),
        ({"response": RESPONSE[:79]}, "needs at least 80"),
        ({"response": [2.0] * 120}, "does not vary"),
        ({"intercept": False}, "starts explicitly"),
    ],
)
def test_what_a_markov_switching_regression_cannot_take_is_refused(
    arguments: dict[str, object], match: str
) -> None:
    call: dict[str, object] = {
        "response": RESPONSE,
        "regressors": [REGRESSOR],
        "intercept": True,
        "states": 2,
        "start": START,
        "tolerance": TOLERANCE,
        "max_iterations": ITERATIONS,
        "step": STEP,
        "maximum_condition": CONDITION,
        **arguments,
    }
    with pytest.raises(EconometricsInputError, match=match):
        fit_markov_switching_regression(**call)  # type: ignore[arg-type]


def test_a_viterbi_tie_goes_to_the_lower_state() -> None:
    """Two states alike in every density and transition: each step's tie goes to state 1."""

    parameters = _Parameters(
        coefficients=[[-1.0], [1.0]],
        variances=[1.0, 1.0],
        transition=[[0.5, 0.5], [0.5, 0.5]],
        initial=[0.5, 0.5],
    )
    log_densities = [[-1.0, -1.0], [-2.0, -2.0], [-1.5, -1.5]]
    assert _viterbi(log_densities, parameters) == (1, 1, 1)
