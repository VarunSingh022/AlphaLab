"""Three deterministic adaptive rules, each learning with the cadence it is given.

Each rule splits learning into the two halves :mod:`alphalab.strategy.adaptive`
asks for: ``observe`` records an observation in a *pending* part of the state,
and ``adapt`` folds everything pending into what the rule has learned. Under
``EVERY_OBSERVATION`` the two happen together and each rule is its textbook
form; under a slower cadence no observation is lost -- it waits in the pending
part until the next adaptation. Every parameter is required, and one the rule
does not read is refused, the v3.2 rule for feature definitions: an unread
parameter would still change the configuration's identity.

=========================== ============================= ======================
Rule                        Learns                        Decides
=========================== ============================= ======================
:class:`ExponentialMeanRule` an exponentially weighted     the mean and the
                            mean                          deviation from it
:class:`TrailingZScoreRule` the last ``window`` values    a z-score against them
                                                          and a signal
:class:`RecursiveLeastSquaresRule` an intercept and slope, a prediction, its
                            with exponential forgetting   residual, the
                                                          coefficients
=========================== ============================= ======================

The z-score's dispersion is :func:`alphalab.common.statistics.standard_deviation`
over the stored window, not a running variance: AlphaLab has one home for the
sample variance, and an incremental second copy would agree with it on every
test value and diverge in the last digits of a long stream. Every rule returns
no output at all while what it would report is undefined -- a mean of nothing,
a z-score over a constant window -- never a zero.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from alphalab.common.statistics import mean, standard_deviation
from alphalab.strategy.adaptive import (
    AdaptiveConfiguration,
    AdaptiveObservation,
    StateValue,
)
from alphalab.strategy.exceptions import AdaptiveStateError

__all__ = [
    "ExponentialMeanRule",
    "RecursiveLeastSquaresRule",
    "TrailingZScoreRule",
]

_EMPTY: Final[tuple[float, ...]] = ()


def _require_shape(
    configuration: AdaptiveConfiguration, rule_id: str, inputs: int, parameters: set[str]
) -> None:
    if len(configuration.inputs) != inputs:
        raise AdaptiveStateError(
            f"{rule_id} reads {inputs} input(s); the configuration names "
            f"{list(configuration.inputs)}."
        )
    given = set(configuration.parameters)
    if given != parameters:
        raise AdaptiveStateError(
            f"{rule_id} reads exactly the parameters {sorted(parameters)}; the configuration "
            f"gives {sorted(given)}. A missing one has no default, and an unread one would "
            "still change the configuration's identity."
        )


def _number(payload: Mapping[str, StateValue], key: str) -> float:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AdaptiveStateError(f"State value {key!r} is {value!r}, not a number.")
    return float(value)


def _count(payload: Mapping[str, StateValue], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdaptiveStateError(f"State value {key!r} is {value!r}, not a count.")
    return value


def _series(payload: Mapping[str, StateValue], key: str) -> tuple[float, ...]:
    value = payload[key]
    if not isinstance(value, tuple):
        raise AdaptiveStateError(f"State value {key!r} is {value!r}, not a sequence.")
    return value


@dataclass(frozen=True, slots=True)
class ExponentialMeanRule:
    """An exponentially weighted mean of one input.

    Parameters: ``smoothing`` in ``(0, 1]`` -- the weight a new value receives.
    The first adaptation seeds the mean with the pending values' mean; each
    later one moves it toward the pending mean by ``smoothing``.
    """

    @property
    def rule_id(self) -> str:
        """``"exponential_mean"``."""

        return "exponential_mean"

    @property
    def rule_version(self) -> int:
        """``1``."""

        return 1

    def validate(self, configuration: AdaptiveConfiguration) -> None:
        """Refuse a configuration this rule cannot read."""

        _require_shape(configuration, self.rule_id, 1, {"smoothing"})
        smoothing = configuration.parameters["smoothing"]
        if not 0.0 < smoothing <= 1.0:
            raise AdaptiveStateError(f"smoothing {smoothing!r} must lie in (0, 1].")

    def initial(self, configuration: AdaptiveConfiguration) -> Mapping[str, StateValue]:
        """Nothing seen, nothing learned."""

        return {"mean": 0.0, "count": 0, "pending_sum": 0.0, "pending_count": 0}

    def observe(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, StateValue]:
        """Add the value to the pending sum."""

        value = observation.values[configuration.inputs[0]]
        return {
            **payload,
            "pending_sum": _number(payload, "pending_sum") + value,
            "pending_count": _count(payload, "pending_count") + 1,
        }

    def adapt(
        self, payload: Mapping[str, StateValue], configuration: AdaptiveConfiguration
    ) -> Mapping[str, StateValue]:
        """Fold the pending values into the mean."""

        pending = _count(payload, "pending_count")
        if pending == 0:
            return payload
        batch = _number(payload, "pending_sum") / pending
        count = _count(payload, "count")
        current = _number(payload, "mean")
        smoothing = configuration.parameters["smoothing"]
        updated = batch if count == 0 else current + smoothing * (batch - current)
        return {"mean": updated, "count": count + pending, "pending_sum": 0.0, "pending_count": 0}

    def decide(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, float]:
        """The mean and the value's deviation from it, once there is a mean."""

        if _count(payload, "count") == 0:
            return {}
        level = _number(payload, "mean")
        return {"mean": level, "deviation": observation.values[configuration.inputs[0]] - level}


@dataclass(frozen=True, slots=True)
class TrailingZScoreRule:
    """A z-score of one input against its own trailing window.

    Parameters: ``window``, a whole number of at least 2 values kept; and
    ``entry``, the absolute z-score beyond which the signal is ``-1`` (above the
    mean) or ``+1`` (below it) -- a mean-reversion signal, ``0`` inside.
    """

    @property
    def rule_id(self) -> str:
        """``"trailing_zscore"``."""

        return "trailing_zscore"

    @property
    def rule_version(self) -> int:
        """``1``."""

        return 1

    def validate(self, configuration: AdaptiveConfiguration) -> None:
        """Refuse a configuration this rule cannot read."""

        _require_shape(configuration, self.rule_id, 1, {"window", "entry"})
        window = configuration.parameters["window"]
        if window != math.floor(window) or window < 2:
            raise AdaptiveStateError(f"window {window!r} must be a whole number of at least 2.")
        if configuration.parameters["entry"] <= 0.0:
            raise AdaptiveStateError("entry must be positive.")

    def initial(self, configuration: AdaptiveConfiguration) -> Mapping[str, StateValue]:
        """An empty window."""

        return {"values": _EMPTY, "pending": _EMPTY}

    def observe(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, StateValue]:
        """Hold the value until the next adaptation."""

        value = float(observation.values[configuration.inputs[0]])
        return {**payload, "pending": (*_series(payload, "pending"), value)}

    def adapt(
        self, payload: Mapping[str, StateValue], configuration: AdaptiveConfiguration
    ) -> Mapping[str, StateValue]:
        """Move the pending values into the window, keeping the last ``window``."""

        keep = int(configuration.parameters["window"])
        joined = (*_series(payload, "values"), *_series(payload, "pending"))
        return {"values": joined[-keep:], "pending": _EMPTY}

    def decide(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, float]:
        """The value's z-score against the window, and the signal it implies.

        Nothing while the window holds fewer than two values or is constant: a
        z-score has no scale then, and zero would read as "at the mean".
        """

        window = _series(payload, "values")
        if len(window) < 2:
            return {}
        deviation = standard_deviation(window)
        if deviation == 0.0:
            return {}
        center = mean(window)
        score = (observation.values[configuration.inputs[0]] - center) / deviation
        entry = configuration.parameters["entry"]
        signal = -1.0 if score >= entry else (1.0 if score <= -entry else 0.0)
        return {"zscore": score, "signal": signal, "mean": center}


@dataclass(frozen=True, slots=True)
class RecursiveLeastSquaresRule:
    """``target = intercept + slope * regressor``, estimated recursively.

    The textbook recursive least-squares update with exponential forgetting --
    an adaptive hedge ratio, a drifting beta. Inputs: the target, then the
    regressor. Parameters: ``forgetting`` in ``(0, 1]`` (``1`` weighs all history
    alike) and ``prior_variance`` > 0, the initial uncertainty on both
    coefficients. The 2x2 covariance is held row-major as four floats.
    """

    @property
    def rule_id(self) -> str:
        """``"recursive_least_squares"``."""

        return "recursive_least_squares"

    @property
    def rule_version(self) -> int:
        """``1``."""

        return 1

    def validate(self, configuration: AdaptiveConfiguration) -> None:
        """Refuse a configuration this rule cannot read."""

        _require_shape(configuration, self.rule_id, 2, {"forgetting", "prior_variance"})
        forgetting = configuration.parameters["forgetting"]
        if not 0.0 < forgetting <= 1.0:
            raise AdaptiveStateError(f"forgetting {forgetting!r} must lie in (0, 1].")
        if configuration.parameters["prior_variance"] <= 0.0:
            raise AdaptiveStateError("prior_variance must be positive.")

    def initial(self, configuration: AdaptiveConfiguration) -> Mapping[str, StateValue]:
        """Zero coefficients with the stated prior uncertainty."""

        prior = float(configuration.parameters["prior_variance"])
        return {
            "intercept": 0.0,
            "slope": 0.0,
            "covariance": (prior, 0.0, 0.0, prior),
            "count": 0,
            "pending": _EMPTY,
        }

    def observe(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, StateValue]:
        """Hold the ``(target, regressor)`` pair until the next adaptation."""

        target, regressor = (float(observation.values[name]) for name in configuration.inputs)
        return {**payload, "pending": (*_series(payload, "pending"), target, regressor)}

    def adapt(
        self, payload: Mapping[str, StateValue], configuration: AdaptiveConfiguration
    ) -> Mapping[str, StateValue]:
        """Apply the recursive update to every pending pair, in order."""

        forgetting = configuration.parameters["forgetting"]
        intercept = _number(payload, "intercept")
        slope = _number(payload, "slope")
        p00, p01, p10, p11 = _series(payload, "covariance")
        pending = _series(payload, "pending")
        count = _count(payload, "count")
        for position in range(0, len(pending), 2):
            target, regressor = pending[position], pending[position + 1]
            gain_0 = p00 + p01 * regressor
            gain_1 = p10 + p11 * regressor
            denominator = forgetting + gain_0 + regressor * gain_1
            k0, k1 = gain_0 / denominator, gain_1 / denominator
            error = target - (intercept + slope * regressor)
            intercept += k0 * error
            slope += k1 * error
            row_0 = p00 + regressor * p10
            row_1 = p01 + regressor * p11
            p00, p01, p10, p11 = (
                (p00 - k0 * row_0) / forgetting,
                (p01 - k0 * row_1) / forgetting,
                (p10 - k1 * row_0) / forgetting,
                (p11 - k1 * row_1) / forgetting,
            )
            count += 1
        return {
            "intercept": intercept,
            "slope": slope,
            "covariance": (p00, p01, p10, p11),
            "count": count,
            "pending": _EMPTY,
        }

    def decide(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, float]:
        """A prediction for the observation's regressor and the residual against its target.

        Nothing until two pairs have been folded in: one point determines no
        line.
        """

        if _count(payload, "count") < 2:
            return {}
        target, regressor = (float(observation.values[name]) for name in configuration.inputs)
        intercept = _number(payload, "intercept")
        slope = _number(payload, "slope")
        prediction = intercept + slope * regressor
        return {
            "prediction": prediction,
            "residual": target - prediction,
            "slope": slope,
            "intercept": intercept,
        }
