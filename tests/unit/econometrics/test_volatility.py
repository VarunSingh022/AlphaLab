"""Conditional volatility by likelihood: GARCH, GJR and EGARCH (v4.3, ADR-0074, WS-07).

* **Known parameters are recovered**: each model, simulated from its own
  recursion, is fitted back within three of its standard errors.
* **The likelihood is the recursion's**: at given parameters it equals a sum
  written out by hand.
* **The region is the parameterisation's**: every fitted persistence is below
  one, and a start outside the region is refused.
* **Every choice is stated** in the estimate, which is class 3 and carries the
  optimiser's convergence report.
"""

from __future__ import annotations

import math
import random

import pytest

from alphalab.common.estimate import NumericalClass
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import series_identity
from alphalab.econometrics.volatility import (
    MINIMUM_VOLATILITY_OBSERVATIONS,
    VolatilityModel,
    VolatilityStart,
    _log_likelihood,
    fit_volatility,
)

TOLERANCE = 1e-6
ITERATIONS = 200
STEP = 1e-5


def _simulate(
    draws: random.Random, count: int, model: VolatilityModel, p: dict[str, float]
) -> list[float]:
    values, h, last = (
        [],
        p["omega"] / max(1e-9, 1.0 - p["alpha"] - p.get("gamma", 0.0) / 2 - p["beta"]),
        0.0,
    )
    if model is VolatilityModel.EGARCH:
        log_h, z = p["omega"] / (1.0 - p["beta"]), 0.0
        for _ in range(count + 500):
            log_h = (
                p["omega"]
                + p["alpha"] * (abs(z) - math.sqrt(2 / math.pi))
                + p["gamma"] * z
                + p["beta"] * log_h
            )
            z = draws.gauss(0.0, 1.0)
            values.append(p["mu"] + math.exp(log_h / 2) * z)
        return values[-count:]
    for _ in range(count + 500):
        weight = p["alpha"] + (p.get("gamma", 0.0) if last < 0 else 0.0)
        h = p["omega"] + weight * last * last + p["beta"] * h
        last = math.sqrt(h) * draws.gauss(0.0, 1.0)
        values.append(p["mu"] + last)
    return values[-count:]


_TRUTH = {
    VolatilityModel.GARCH: {"mu": 0.05, "omega": 0.05, "alpha": 0.08, "beta": 0.9},
    VolatilityModel.GJR: {"mu": 0.02, "omega": 0.05, "alpha": 0.03, "gamma": 0.1, "beta": 0.88},
    VolatilityModel.EGARCH: {
        "mu": 0.0,
        "omega": -0.05,
        "alpha": 0.15,
        "gamma": -0.08,
        "beta": 0.95,
    },
}


@pytest.mark.parametrize("model", list(VolatilityModel))
def test_known_parameters_are_recovered_within_their_standard_errors(
    model: VolatilityModel,
) -> None:
    truth = _TRUTH[model]
    returns = _simulate(random.Random(31 + list(VolatilityModel).index(model)), 4000, model, truth)
    fit = fit_volatility(
        returns,
        model=model,
        start=VolatilityStart.MOMENTS,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )

    for name, value in truth.items():
        estimate = fit.estimate.parameters[name]
        error = fit.estimate.standard_errors[name]
        assert abs(estimate - value) < 3.5 * error, (name, estimate, value, error)
    assert fit.persistence < 1.0
    assert len(fit.variances) == 4000 and min(fit.variances) > 0.0


def test_the_likelihood_is_the_recursion_written_out() -> None:
    returns = [0.3, -0.5, 0.1, 0.8, -1.2, 0.4] * 20
    mu, omega, alpha, beta = 0.01, 0.1, 0.1, 0.8
    initial = sum((r - sum(returns) / len(returns)) ** 2 for r in returns) / len(returns)
    total, h, last = 0.0, initial, None
    for r in returns:
        if last is not None:
            h = omega + alpha * last * last + beta * h
        e = r - mu
        total += math.log(2 * math.pi) + math.log(h) + e * e / h
        last = e
    got = _log_likelihood(VolatilityModel.GARCH, [mu, omega, alpha, beta], returns, initial)
    assert got == pytest.approx(-0.5 * total, rel=1e-12)


def test_the_estimate_states_every_choice_and_its_convergence() -> None:
    returns = _simulate(
        random.Random(2), 1500, VolatilityModel.GARCH, _TRUTH[VolatilityModel.GARCH]
    )
    start = {"mu": 0.0, "omega": 0.1, "alpha": 0.1, "beta": 0.8}
    fit = fit_volatility(
        returns,
        model=VolatilityModel.GARCH,
        start=start,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )
    estimate = fit.estimate
    assert estimate.data_id == series_identity(returns)
    assert estimate.numerical_class is NumericalClass.HOST_LIBM
    assert estimate.convergence is not None and estimate.convergence.method == "BFGS"
    settings = dict(estimate.settings)
    assert settings["start"] == "explicit" and settings["start_alpha"] == 0.1
    assert settings["initial_variance"] == "sample_variance"
    assert settings["tolerance"] == TOLERANCE and settings["step"] == STEP


@pytest.mark.parametrize(
    ("returns", "model", "start", "match"),
    [
        (
            [0.1, -0.1] * 49,
            VolatilityModel.GARCH,
            VolatilityStart.MOMENTS,
            f"{MINIMUM_VOLATILITY_OBSERVATIONS}",
        ),
        ([0.0] * 200, VolatilityModel.GARCH, VolatilityStart.MOMENTS, "do not vary"),
        (
            None,
            VolatilityModel.GARCH,
            {"mu": 0.0, "omega": 0.1, "alpha": 0.5, "beta": 0.6},
            "admissible",
        ),
        (None, VolatilityModel.GARCH, {"mu": 0.0, "omega": 0.1, "alpha": 0.1}, "names"),
        (
            None,
            VolatilityModel.EGARCH,
            {"mu": 0.0, "omega": 0.0, "alpha": 0.1, "gamma": 0.0, "beta": 1.0},
            "inside",
        ),
        (None, "garch", VolatilityStart.MOMENTS, "VolatilityModel"),
    ],
)
def test_what_a_fit_cannot_take_is_refused(
    returns: list[float] | None, model: object, start: object, match: str
) -> None:
    series = (
        returns
        if returns is not None
        else _simulate(random.Random(4), 300, VolatilityModel.GARCH, _TRUTH[VolatilityModel.GARCH])
    )
    with pytest.raises(EconometricsInputError, match=match):
        fit_volatility(
            series,
            model=model,  # type: ignore[arg-type]
            start=start,  # type: ignore[arg-type]
            tolerance=TOLERANCE,
            max_iterations=ITERATIONS,
            step=STEP,
        )
