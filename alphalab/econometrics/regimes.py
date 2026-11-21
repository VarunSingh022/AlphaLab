"""Statistical regime models: hidden Markov and Markov-switching estimation (v4.3, ADR-0075).

Two models, each fitted by expectation-maximisation (OFE-010, reopened by
ADR-0075):

* :func:`fit_gaussian_hmm`, ADR-0075's ``GaussianHMM``: ``K`` hidden states,
  each with its own mean and variance, and a first-order Markov chain between
  them, by Baum and Welch's recursions, scaled (Rabiner, 1989).
* :func:`fit_markov_switching_regression`, ADR-0075's
  ``MarkovSwitchingRegression``: a response on the caller's regressors, its
  coefficients and variance switching with the state, by Hamilton's filter
  (1989) and Kim's smoother (1994), with weighted least squares per state. A
  Markov-switching autoregression in the switching-intercept form is this
  model with lagged responses among the regressors.

The expectation step
--------------------
One recursion serves both. Forward, it is Hamilton's filter: the state the
chain predicts, weighted by each state's density and normalised. The
normaliser ``c_t`` is the observation's density given the past, so the
log-likelihood is ``sum log c_t`` (Rabiner's scaling). Backward, Rabiner's
scaled recursion gives each instant's smoothed probabilities and each
transition's: Kim's smoother's posterior, computed without dividing by a
predicted probability that may be zero. Each instant's densities are scaled
by their largest before ``exp``, which the normaliser absorbs, so none
underflows.

The maximisation step
---------------------
The initial distribution is the first instant's smoothed probabilities; each
row of the transition matrix is its expected transitions over their total; a
state's mean, or coefficients by M2's least squares on rows weighted by the
square roots of its probabilities, and its variance are its weighted moments.

Every choice is stated
----------------------
``K``, two at least; the start, explicit or :class:`QuantileStart`; the
tolerance on the log-likelihood's absolute change and the iterations allowed;
the step of the differences the standard errors take. EM finds a local
maximum, and the estimate records the start that reached it: the package
never restarts on its own, which would be a search nobody counts.

What is refused, never repaired
-------------------------------
A series too short for ``K`` states and their coefficients; a start whose
states coincide, which EM cannot separate; a state whose variance collapses
to zero, or that is left with no observation
(:class:`~alphalab.econometrics.exceptions.CollapsedStateError`, naming the
state); a fit that does not converge within its limit (``ConvergenceError``,
with the log-likelihood reached).

One labelling
-------------
The states are ordered by ascending mean -- for the regression, ascending
variance -- ties broken by variance, then by mean (for the regression, by the
coefficients in order). A labelling convention: it changes no fitted value.

Standard errors
---------------
From the observed information at the maximum: the log-likelihood's curvature
by central differences, inverted through M2's Cholesky factor. Its
coordinates are each mean or coefficient over a scale the data fix (the
response's standard deviation over the regressor's root mean square), each
variance's logarithm, and the logarithm of each transition probability but
the largest in its row, which the row's sum determines; the delta method maps
them back. A transition at exactly zero -- a structural zero an explicit start
stated, which EM keeps -- is fixed, and has none. The initial distribution is
the first instant's posterior, at its boundary as a rule: it is held, and has
none. An information that is not positive definite is refused, naming the
smallest free transition: a transition the data do not show is a structural
zero, stated as one in an explicit start.

Classes
-------
Every value passes through ``exp`` and ``log``: class 3, recorded beside the
identity, which covers the problem -- the data, ``K``, the model, the start,
the tolerance and the limit (ADR-0058).
"""

from __future__ import annotations

import math
import operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.estimate import Estimate, NumericalClass, Setting
from alphalab.common.exceptions import ConvergenceError
from alphalab.common.linalg import IllConditionedError, least_squares
from alphalab.econometrics._likelihood import curvature_covariance
from alphalab.econometrics.exceptions import CollapsedStateError, EconometricsInputError
from alphalab.econometrics.series import require_series, series_identity, system_identity

__all__ = [
    "GAUSSIAN_HMM_METHOD",
    "MARKOV_SWITCHING_METHOD",
    "MINIMUM_REGIME_OBSERVATIONS",
    "QuantileStart",
    "RegimeFit",
    "fit_gaussian_hmm",
    "fit_markov_switching_regression",
]

#: The Gaussian HMM's method scheme: Baum-Welch, scaled.
GAUSSIAN_HMM_METHOD: Final = "alphalab.econometrics.regimes.fit_gaussian_hmm/baum-welch.v1"

#: The Markov-switching regression's: Hamilton's filter, Kim's smoother, EM.
MARKOV_SWITCHING_METHOD: Final = (
    "alphalab.econometrics.regimes.fit_markov_switching_regression/hamilton-kim-em.v1"
)

#: The observations a fit needs for every state and every coefficient it fits there.
MINIMUM_REGIME_OBSERVATIONS: Final = 20

_LOG_TWO_PI: Final = math.log(2.0 * math.pi)
_CRITERION: Final = "the absolute change of the log-likelihood"


@dataclass(frozen=True, slots=True)
class QuantileStart:
    """ADR-0075's named start rule.

    Each state's mean starts at one of the data's ``K`` quantiles and every
    variance at the sample's. For the regression, which needs an intercept,
    each state's intercept starts at the pooled least-squares intercept plus
    one of its residuals' quantiles, its slopes at the pooled slopes, and every
    variance at the pooled residuals'. Quantile ``k`` of ``K``, from zero, is
    the sorted values' entry ``floor((k + 1/2) n / K)``: a value of the data,
    exactly. The chain starts with ``persistence`` on its diagonal and the rest
    of each row spread evenly; the initial distribution is even.

    Attributes:
        persistence: Each state's probability of staying, in ``(0, 1)``.

    Raises:
        EconometricsInputError: If ``persistence`` is not a number in ``(0, 1)``.
    """

    persistence: float

    def __post_init__(self) -> None:
        value = self.persistence
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 < value < 1.0:
            raise EconometricsInputError(f"persistence is a probability in (0, 1), got {value!r}.")
        object.__setattr__(self, "persistence", float(value))


@dataclass(frozen=True, slots=True)
class RegimeFit:
    """A regime model fitted by EM: its estimate, its states' probabilities and its path.

    Attributes:
        estimate: Each state's mean or coefficients and variance, the
            transition matrix and the initial distribution, with standard
            errors; class 3, with EM's convergence report.
        log_likelihood: At the estimate.
        states: ``K``.
        filtered: ``P(S_t = k | y_1 .. y_t)`` at each instant: entry ``k - 1``
            is the estimate's state ``k``.
        smoothed: ``P(S_t = k | y_1 .. y_T)``, likewise.
        path: The most likely sequence of states, by Viterbi's algorithm,
            numbered from one as the estimate's are.
    """

    estimate: Estimate
    log_likelihood: float
    states: int
    filtered: tuple[tuple[float, ...], ...]
    smoothed: tuple[tuple[float, ...], ...]
    path: tuple[int, ...]


@dataclass(slots=True)
class _Parameters:
    coefficients: list[list[float]]
    variances: list[float]
    transition: list[list[float]]
    initial: list[float]


@dataclass(slots=True)
class _Posterior:
    log_likelihood: float
    log_densities: list[list[float]]
    filtered: list[list[float]]
    smoothed: list[list[float]]
    transitions: list[list[float]]


@dataclass(frozen=True, slots=True)
class _Model:
    """The data and what the model fits to them."""

    rows: tuple[tuple[float, ...], ...]
    values: tuple[float, ...]
    coefficient_names: tuple[str, ...]
    scales: tuple[float, ...]
    regression: bool
    maximum_condition: float
    description: str

    def names(self, states: int) -> list[str]:
        return [
            *(
                f"state_{k + 1}_{name}"
                for k in range(states)
                for name in (*self.coefficient_names, "variance")
            ),
            *(f"transition_{j + 1}_{k + 1}" for j in range(states) for k in range(states)),
            *(f"initial_{k + 1}" for k in range(states)),
        ]

    def flatten(self, parameters: _Parameters) -> dict[str, float]:
        states = len(parameters.variances)
        values = [
            *(
                value
                for k in range(states)
                for value in (*parameters.coefficients[k], parameters.variances[k])
            ),
            *(value for row in parameters.transition for value in row),
            *parameters.initial,
        ]
        return dict(zip(self.names(states), values, strict=True))


def _log_densities(model: _Model, parameters: _Parameters) -> list[list[float]]:
    logs = [math.log(variance) for variance in parameters.variances]
    states = range(len(logs))
    densities = []
    for row, value in zip(model.rows, model.values, strict=True):
        instant = []
        for k in states:
            residual = value - math.fsum(map(operator.mul, row, parameters.coefficients[k]))
            instant.append(
                -0.5 * (_LOG_TWO_PI + logs[k] + residual * residual / parameters.variances[k])
            )
        densities.append(instant)
    return densities


def _forward(
    log_densities: Sequence[Sequence[float]], parameters: _Parameters
) -> tuple[float, list[list[float]], list[list[float]], list[float]]:
    """Hamilton's filter: the log-likelihood, the filtered probabilities, the scaled densities."""

    transition = parameters.transition
    states = range(len(parameters.initial))
    filtered: list[list[float]] = []
    scaled_densities: list[list[float]] = []
    normalisers: list[float] = []
    terms = []
    predicted: Sequence[float] = parameters.initial
    for t, logs in enumerate(log_densities):
        top = max(logs)
        scaled = [math.exp(value - top) for value in logs]
        if filtered:
            previous = filtered[-1]
            predicted = [
                math.fsum([previous[j] * transition[j][k] for j in states]) for k in states
            ]
        joint = [p * b for p, b in zip(predicted, scaled, strict=True)]
        total = math.fsum(joint)
        if not total > 0.0:
            raise EconometricsInputError(
                f"Observation {t + 1} has probability zero: the chain allows no state there "
                "with any density. Restate the start's structural zeros."
            )
        filtered.append([value / total for value in joint])
        scaled_densities.append(scaled)
        normalisers.append(total)
        terms.append(math.log(total) + top)
    return math.fsum(terms), filtered, scaled_densities, normalisers


def _posterior(model: _Model, parameters: _Parameters) -> _Posterior:
    """Hamilton's filter forward, Rabiner's scaled recursion back: Kim's smoothed probabilities."""

    log_densities = _log_densities(model, parameters)
    log_likelihood, filtered, scaled, normalisers = _forward(log_densities, parameters)
    transition = parameters.transition
    states = range(len(transition))
    count = len(filtered)
    smoothed = [filtered[-1]] * count
    pieces: list[list[list[float]]] = [[[] for _ in states] for _ in states]
    backward = [1.0] * len(transition)
    for t in range(count - 2, -1, -1):
        weighted = [scaled[t + 1][k] * backward[k] / normalisers[t + 1] for k in states]
        for j in states:
            for k in states:
                pieces[j][k].append(filtered[t][j] * transition[j][k] * weighted[k])
        backward = [math.fsum([transition[j][k] * weighted[k] for k in states]) for j in states]
        smoothed[t] = [filtered[t][j] * backward[j] for j in states]
    return _Posterior(
        log_likelihood=log_likelihood,
        log_densities=log_densities,
        filtered=filtered,
        smoothed=smoothed,
        transitions=[[math.fsum(pieces[j][k]) for k in states] for j in states],
    )


def _maximise(
    model: _Model, posterior: _Posterior, iteration: int, tolerance: float, limit: int
) -> _Parameters:
    states = range(len(posterior.transitions))
    transition = []
    for j in states:
        total = math.fsum(posterior.transitions[j])
        if not total > 0.0:
            raise CollapsedStateError(
                model.description,
                tolerance,
                limit,
                state=j + 1,
                variance=math.nan,
                iteration=iteration,
            )
        transition.append([count / total for count in posterior.transitions[j]])
    coefficients, variances = [], []
    for k in states:
        weights = [instant[k] for instant in posterior.smoothed]
        mass = math.fsum(weights)
        if not mass > 0.0:
            raise CollapsedStateError(
                model.description,
                tolerance,
                limit,
                state=k + 1,
                variance=math.nan,
                iteration=iteration,
            )
        fitted = _weighted(model, weights, mass, k)
        variance = (
            math.fsum(
                [
                    w * (y - math.fsum(map(operator.mul, row, fitted))) ** 2
                    for w, row, y in zip(weights, model.rows, model.values, strict=True)
                ]
            )
            / mass
        )
        if not (math.isfinite(variance) and variance > 0.0):
            raise CollapsedStateError(
                model.description,
                tolerance,
                limit,
                state=k + 1,
                variance=variance,
                iteration=iteration,
            )
        coefficients.append(fitted)
        variances.append(variance)
    return _Parameters(coefficients, variances, transition, list(posterior.smoothed[0]))


def _weighted(model: _Model, weights: Sequence[float], mass: float, state: int) -> list[float]:
    if not model.regression:
        return [math.fsum(map(operator.mul, weights, model.values)) / mass]
    roots = [math.sqrt(w) for w in weights]
    design = [[r * x for x in row] for r, row in zip(roots, model.rows, strict=True)]
    target = [r * y for r, y in zip(roots, model.values, strict=True)]
    try:
        return list(least_squares(design, target, model.maximum_condition).coefficients)
    except IllConditionedError as error:
        raise IllConditionedError(
            f"State {state + 1}'s weighted regression is conditioned at {error.condition:.6g}, "
            f"beyond the stated maximum of {error.maximum:g}: the observations it holds do "
            "not fix its coefficients.",
            error.condition,
            error.maximum,
        ) from error


def _em(
    model: _Model, start: _Parameters, tolerance: float, limit: int
) -> tuple[_Parameters, _Posterior, ConvergenceReport]:
    parameters = start
    posterior = _posterior(model, parameters)
    change = math.inf
    for iteration in range(1, limit + 1):
        parameters = _maximise(model, posterior, iteration, tolerance, limit)
        updated = _posterior(model, parameters)
        if not math.isfinite(updated.log_likelihood):
            smallest = min(range(len(parameters.variances)), key=parameters.variances.__getitem__)
            raise CollapsedStateError(
                model.description,
                tolerance,
                limit,
                state=smallest + 1,
                variance=parameters.variances[smallest],
                iteration=iteration,
            )
        change = abs(updated.log_likelihood - posterior.log_likelihood)
        posterior = updated
        if change <= tolerance:
            report = ConvergenceReport(
                model.description, _CRITERION, tolerance, limit, iteration, change
            )
            return parameters, posterior, report
    smallest = min(range(len(parameters.variances)), key=parameters.variances.__getitem__)
    raise ConvergenceError(
        f"{model.description} (the log-likelihood at {posterior.log_likelihood!r}; the "
        f"smallest variance state {smallest + 1}'s, {parameters.variances[smallest]!r})",
        tolerance,
        limit,
        change,
    )


def _ordered(
    model: _Model, parameters: _Parameters, posterior: _Posterior
) -> tuple[_Parameters, _Posterior]:
    """ADR-0075's labelling: ascending mean (regression: variance), then the other."""

    states = range(len(parameters.variances))
    if model.regression:
        order = sorted(
            states, key=lambda k: (parameters.variances[k], tuple(parameters.coefficients[k]))
        )
    else:
        order = sorted(
            states, key=lambda k: (parameters.coefficients[k][0], parameters.variances[k])
        )
    permuted = _Parameters(
        coefficients=[parameters.coefficients[k] for k in order],
        variances=[parameters.variances[k] for k in order],
        transition=[[parameters.transition[j][k] for k in order] for j in order],
        initial=[parameters.initial[k] for k in order],
    )
    return permuted, _Posterior(
        log_likelihood=posterior.log_likelihood,
        log_densities=[[instant[k] for k in order] for instant in posterior.log_densities],
        filtered=[[instant[k] for k in order] for instant in posterior.filtered],
        smoothed=[[instant[k] for k in order] for instant in posterior.smoothed],
        transitions=[[posterior.transitions[j][k] for k in order] for j in order],
    )


def _viterbi(log_densities: Sequence[Sequence[float]], parameters: _Parameters) -> tuple[int, ...]:
    """The most likely path, in logarithms; a tie goes to the lower state."""

    def log(probability: float) -> float:
        return math.log(probability) if probability > 0.0 else -math.inf

    states = range(len(parameters.initial))
    log_transition = [[log(p) for p in row] for row in parameters.transition]
    scores = [log(parameters.initial[k]) + log_densities[0][k] for k in states]
    pointers: list[list[int]] = []
    for logs in log_densities[1:]:
        best_from, updated = [], []
        for k in states:
            best, chosen = scores[0] + log_transition[0][k], 0
            for j in states[1:]:
                value = scores[j] + log_transition[j][k]
                if value > best:
                    best, chosen = value, j
            best_from.append(chosen)
            updated.append(best + logs[k])
        pointers.append(best_from)
        scores = updated
    state = max(states, key=scores.__getitem__)
    path = [state]
    for best_from in reversed(pointers):
        state = best_from[state]
        path.append(state)
    path.reverse()
    return tuple(state + 1 for state in path)


def _standard_errors(model: _Model, parameters: _Parameters, step: float) -> dict[str, float]:
    states = len(parameters.variances)
    width = len(model.scales)
    transition = parameters.transition
    references = [max(range(states), key=row.__getitem__) for row in transition]
    free = [
        (j, k)
        for j in range(states)
        for k in range(states)
        if k != references[j] and transition[j][k] > 0.0
    ]
    point = [
        *(
            parameters.coefficients[k][i] / model.scales[i]
            for k in range(states)
            for i in range(width)
        ),
        *(math.log(variance) for variance in parameters.variances),
        *(math.log(transition[j][k]) for j, k in free),
    ]
    variance_at = states * width
    transition_at = variance_at + states

    def negative_log_likelihood(coordinates: Sequence[float]) -> float:
        moved = [list(row) for row in transition]
        for index, (j, k) in enumerate(free):
            moved[j][k] = math.exp(coordinates[transition_at + index])
        for j, reference in enumerate(references):
            moved[j][reference] = 1.0 - math.fsum(
                [p for k, p in enumerate(moved[j]) if k != reference]
            )
        trial = _Parameters(
            coefficients=[
                [coordinates[k * width + i] * model.scales[i] for i in range(width)]
                for k in range(states)
            ],
            variances=[math.exp(coordinates[variance_at + k]) for k in range(states)],
            transition=moved,
            initial=parameters.initial,
        )
        return -_forward(_log_densities(model, trial), trial)[0]

    try:
        covariance = curvature_covariance(negative_log_likelihood, point, step)
    except EconometricsInputError as error:
        detail = ""
        if free:
            j, k = min(free, key=lambda pair: transition[pair[0]][pair[1]])
            detail = (
                f" The smallest free transition is transition_{j + 1}_{k + 1}, "
                f"{transition[j][k]!r}: a transition the data do not show is a structural "
                "zero, stated as 0 in an explicit start."
            )
        raise EconometricsInputError(f"{error}{detail}") from error
    errors: dict[str, float] = {}
    for k in range(states):
        for i, name in enumerate(model.coefficient_names):
            index = k * width + i
            errors[f"state_{k + 1}_{name}"] = model.scales[i] * math.sqrt(covariance[index][index])
        index = variance_at + k
        errors[f"state_{k + 1}_variance"] = parameters.variances[k] * math.sqrt(
            covariance[index][index]
        )
    for index, (j, k) in enumerate(free):
        at = transition_at + index
        errors[f"transition_{j + 1}_{k + 1}"] = transition[j][k] * math.sqrt(covariance[at][at])
    for j, reference in enumerate(references):
        row = [
            (transition_at + index, transition[a][b]) for index, (a, b) in enumerate(free) if a == j
        ]
        if row:
            # The reference is one less the row's others: d p_r / d ln p_k = -p_k.
            variance = math.fsum([pa * pb * covariance[a][b] for a, pa in row for b, pb in row])
            errors[f"transition_{j + 1}_{reference + 1}"] = math.sqrt(variance)
    return errors


def _quantiles(values: Sequence[float], states: int) -> list[float]:
    ordered = sorted(values)
    count = len(ordered)
    return [ordered[((2 * k + 1) * count) // (2 * states)] for k in range(states)]


def _chain(persistence: float, states: int) -> tuple[list[list[float]], list[float]]:
    spread = (1.0 - persistence) / (states - 1)
    transition = [[persistence if j == k else spread for k in range(states)] for j in range(states)]
    return transition, [1.0 / states] * states


def _explicit(model: _Model, start: Mapping[str, float], states: int) -> _Parameters:
    names = model.names(states)
    if sorted(start) != sorted(names):
        raise EconometricsInputError(
            f"An explicit start names every parameter, {', '.join(names)}; got "
            f"{', '.join(sorted(start))}."
        )
    values = {}
    for name in names:
        value = start[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise EconometricsInputError(f"The start's {name} is {value!r}: not a finite number.")
        values[name] = float(value)
    parameters = _Parameters(
        coefficients=[
            [values[f"state_{k + 1}_{name}"] for name in model.coefficient_names]
            for k in range(states)
        ],
        variances=[values[f"state_{k + 1}_variance"] for k in range(states)],
        transition=[
            [values[f"transition_{j + 1}_{k + 1}"] for k in range(states)] for j in range(states)
        ],
        initial=[values[f"initial_{k + 1}"] for k in range(states)],
    )
    for k, variance in enumerate(parameters.variances):
        if not variance > 0.0:
            raise EconometricsInputError(
                f"The start's state_{k + 1}_variance is {variance!r}: a variance is positive."
            )
    for what, row in [
        *((f"transition row {j + 1}", row) for j, row in enumerate(parameters.transition)),
        ("initial distribution", parameters.initial),
    ]:
        if any(p < 0.0 for p in row) or math.fsum(row) != 1.0:
            raise EconometricsInputError(
                f"The start's {what} is a distribution: non-negative, summing to one exactly "
                f"as its exactly rounded sum, got {row!r}."
            )
    return parameters


def _distinct(parameters: _Parameters) -> None:
    seen = set()
    for k, (coefficients, variance) in enumerate(
        zip(parameters.coefficients, parameters.variances, strict=True)
    ):
        key = (*coefficients, variance)
        if key in seen:
            raise EconometricsInputError(
                f"State {k + 1} starts as another state does: EM cannot separate states that "
                "start alike. Restate the start or the number of states."
            )
        seen.add(key)


def _choices(states: int, tolerance: float, max_iterations: int, step: float) -> None:
    if isinstance(states, bool) or not isinstance(states, int) or states < 2:
        raise EconometricsInputError(f"states is an integer of two at least, got {states!r}.")
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


def _fit(
    model: _Model,
    states: int,
    start: QuantileStart | Mapping[str, float],
    starting: _Parameters,
    *,
    tolerance: float,
    max_iterations: int,
    step: float,
    method: str,
    data_id: str,
    settings: Mapping[str, Setting],
) -> RegimeFit:
    _distinct(starting)
    parameters, posterior, report = _em(model, starting, tolerance, max_iterations)
    parameters, posterior = _ordered(model, parameters, posterior)
    stated: dict[str, Setting] = {
        **settings,
        "max_iterations": max_iterations,
        "observations": len(model.values),
        "states": states,
        "step": float(step),
        "tolerance": float(tolerance),
    }
    if isinstance(start, QuantileStart):
        stated["start"] = "quantile"
        stated["start_persistence"] = start.persistence
    else:
        stated["start"] = "explicit"
        stated.update({f"start_{name}": float(value) for name, value in start.items()})
    estimate = Estimate(
        method=method,
        data_id=data_id,
        parameters=model.flatten(parameters),
        standard_errors=_standard_errors(model, parameters, step),
        settings=stated,
        numerical_class=NumericalClass.HOST_LIBM,
        convergence=report,
    )
    return RegimeFit(
        estimate=estimate,
        log_likelihood=posterior.log_likelihood,
        states=states,
        filtered=tuple(tuple(instant) for instant in posterior.filtered),
        smoothed=tuple(tuple(instant) for instant in posterior.smoothed),
        path=_viterbi(posterior.log_densities, parameters),
    )


def _spread(values: Sequence[float]) -> float:
    count = len(values)
    mean = math.fsum(values) / count
    return math.fsum([(v - mean) ** 2 for v in values]) / count


def fit_gaussian_hmm(
    values: Sequence[float],
    *,
    states: int,
    start: QuantileStart | Mapping[str, float],
    tolerance: float,
    max_iterations: int,
    step: float,
) -> RegimeFit:
    """Fit ADR-0075's ``GaussianHMM`` to ``values`` by Baum and Welch's EM.

    Args:
        values: The series, in time order: 20 for each state at least.
        states: ``K``, two at least.
        start: :class:`QuantileStart`, or every parameter by name --
            ``state_k_mean``, ``state_k_variance``, ``transition_j_k`` and
            ``initial_k``, numbered from one -- each row of the transition matrix
            and the initial distribution summing to one.
        tolerance: The log-likelihood's absolute change that ends EM.
        max_iterations: EM's iterations allowed.
        step: The central differences' step for the standard errors.

    Raises:
        EconometricsInputError: If a value is not finite or the series does
            not vary, it is too short, a choice or the start is malformed, two
            states start alike, or the information at the maximum is not
            positive definite.
        CollapsedStateError: If a state's variance collapses or a state is
            left with no observation.
        ConvergenceError: If EM does not meet its criterion within its limit.
    """

    _choices(states, tolerance, max_iterations, step)
    series = require_series(values, MINIMUM_REGIME_OBSERVATIONS * states, "The series")
    variance = _spread(series)
    if variance == 0.0:
        raise EconometricsInputError("The series does not vary: it has no regimes to find.")
    model = _Model(
        rows=((1.0,),) * len(series),
        values=series,
        coefficient_names=("mean",),
        scales=(math.sqrt(variance),),
        regression=False,
        maximum_condition=1.0,
        description="Expectation-maximisation for a Gaussian hidden Markov model",
    )
    if isinstance(start, QuantileStart):
        transition, initial = _chain(start.persistence, states)
        starting = _Parameters(
            coefficients=[[q] for q in _quantiles(series, states)],
            variances=[variance] * states,
            transition=transition,
            initial=initial,
        )
    elif isinstance(start, Mapping):
        starting = _explicit(model, start, states)
    else:
        raise EconometricsInputError(f"start is a QuantileStart or a mapping, got {start!r}.")
    return _fit(
        model,
        states,
        start,
        starting,
        tolerance=tolerance,
        max_iterations=max_iterations,
        step=step,
        method=GAUSSIAN_HMM_METHOD,
        data_id=series_identity(series),
        settings={},
    )


def fit_markov_switching_regression(
    response: Sequence[float],
    regressors: Sequence[Sequence[float]],
    *,
    intercept: bool,
    states: int,
    start: QuantileStart | Mapping[str, float],
    tolerance: float,
    max_iterations: int,
    step: float,
    maximum_condition: float,
) -> RegimeFit:
    """Fit ADR-0075's ``MarkovSwitchingRegression`` by Hamilton's filter, Kim's smoother and EM.

    Every coefficient and the variance switch with the state. A
    Markov-switching autoregression in the switching-intercept form passes
    the response's own lags as regressors.

    Args:
        response: ``y``, in time order.
        regressors: One series or more, each as long as ``response``.
        intercept: Whether a constant, switching too, is the first coefficient.
        states: ``K``, two at least.
        start: :class:`QuantileStart`, which needs the intercept, or every
            parameter by name -- ``state_k_intercept``, ``state_k_beta_i``,
            ``state_k_variance``, ``transition_j_k`` and ``initial_k``, numbered
            from one -- each distribution summing to one.
        tolerance: The log-likelihood's absolute change that ends EM.
        max_iterations: EM's iterations allowed.
        step: The central differences' step for the standard errors.
        maximum_condition: The largest condition number a least-squares
            design may have: the pooled one, and each state's weighted one.

    Raises:
        EconometricsInputError: If a value is not finite, the series differ in
            length, there is no regressor, the response does not vary, there
            are fewer than 20 observations for each state's every coefficient,
            a choice or the start is malformed, two states start alike, or the
            information at the maximum is not positive definite.
        IllConditionedError: If a design is too ill-conditioned.
        CollapsedStateError: If a state's variance collapses or a state is
            left with no observation.
        ConvergenceError: If EM does not meet its criterion within its limit.
    """

    _choices(states, tolerance, max_iterations, step)
    if not isinstance(intercept, bool):
        raise EconometricsInputError(f"intercept is a bool, got {intercept!r}.")
    if (
        isinstance(maximum_condition, bool)
        or not isinstance(maximum_condition, int | float)
        or not (math.isfinite(maximum_condition) and maximum_condition >= 1.0)
    ):
        raise EconometricsInputError(
            f"maximum_condition is a finite number of 1 at least, got {maximum_condition!r}."
        )
    if not isinstance(regressors, Sequence) or not regressors:
        raise EconometricsInputError(
            "A Markov-switching regression needs one regressor at least; a series alone is "
            "fit_gaussian_hmm's."
        )
    width = len(regressors) + intercept
    ys = require_series(response, MINIMUM_REGIME_OBSERVATIONS * states * width, "The response")
    xs = [require_series(each, 1, f"Regressor {i + 1}") for i, each in enumerate(regressors)]
    for i, each in enumerate(xs):
        if len(each) != len(ys):
            raise EconometricsInputError(
                f"Regressor {i + 1} has {len(each)} values and the response {len(ys)}."
            )
    spread = _spread(ys)
    if spread == 0.0:
        raise EconometricsInputError("The response does not vary: it has no regimes to find.")
    rows = tuple(
        ((1.0,) if intercept else ()) + tuple(each[t] for each in xs) for t in range(len(ys))
    )
    scale = math.sqrt(spread)
    roots = [math.sqrt(math.fsum([x * x for x in each]) / len(each)) for each in xs]
    model = _Model(
        rows=rows,
        values=ys,
        coefficient_names=(
            *(("intercept",) if intercept else ()),
            *(f"beta_{i + 1}" for i in range(len(xs))),
        ),
        scales=(
            *((scale,) if intercept else ()),
            *(scale / root if root > 0.0 else scale for root in roots),
        ),
        regression=True,
        maximum_condition=float(maximum_condition),
        description="Expectation-maximisation for a Markov-switching regression",
    )
    if isinstance(start, QuantileStart):
        if not intercept:
            raise EconometricsInputError(
                "The quantile rule moves each state's intercept: a regression without one "
                "starts explicitly."
            )
        pooled = least_squares([list(row) for row in rows], ys, float(maximum_condition))
        residual_variance = math.fsum([e * e for e in pooled.residuals]) / len(ys)
        transition, initial = _chain(start.persistence, states)
        starting = _Parameters(
            coefficients=[
                [pooled.coefficients[0] + q, *pooled.coefficients[1:]]
                for q in _quantiles(pooled.residuals, states)
            ],
            variances=[residual_variance] * states,
            transition=transition,
            initial=initial,
        )
    elif isinstance(start, Mapping):
        starting = _explicit(model, start, states)
    else:
        raise EconometricsInputError(f"start is a QuantileStart or a mapping, got {start!r}.")
    return _fit(
        model,
        states,
        start,
        starting,
        tolerance=tolerance,
        max_iterations=max_iterations,
        step=step,
        method=MARKOV_SWITCHING_METHOD,
        data_id=system_identity([ys, *xs]),
        settings={
            "intercept": intercept,
            "maximum_condition": float(maximum_condition),
            "regressors": len(xs),
        },
    )
