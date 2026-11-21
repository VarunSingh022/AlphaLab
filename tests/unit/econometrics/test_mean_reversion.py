"""The Ornstein-Uhlenbeck fit and its half-life (v4.3, ADR-0074 section 7, OFE-007).

* **Known parameters are recovered.** A process simulated by its exact
  discretisation is fitted back within its standard errors, and over many
  samples the two-standard-error intervals cover the truth at their rate.
* **Every value is the exact one.** The regression is held to exact rational
  least squares, and each process parameter to its formula in 50 digits.
* **The delta method is the derivative.** The half-life's standard error is
  the regression's, carried by the derivative a finite difference measures.
* **No mean reversion, no half-life.** A coefficient outside ``(0, 1)`` is
  refused, as is every malformed choice.
"""

from __future__ import annotations

import math
import random
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from alphalab.common.linalg import IllConditionedError
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.mean_reversion import (
    MINIMUM_ORNSTEIN_UHLENBECK_OBSERVATIONS,
    fit_ornstein_uhlenbeck,
)
from alphalab.econometrics.series import series_identity

CONDITION = 1e10
DAY = 1.0 / 252


def _simulate(
    draws: random.Random, count: int, theta: float, mu: float, sigma: float
) -> list[float]:
    """The exact discretisation, observed daily."""

    b = math.exp(-theta * DAY)
    scale = sigma * math.sqrt((1.0 - b * b) / (2.0 * theta))
    values = [mu]
    for _ in range(count - 1):
        values.append(mu * (1.0 - b) + b * values[-1] + scale * draws.gauss(0.0, 1.0))
    return values


def test_known_parameters_are_recovered_within_their_standard_errors() -> None:
    values = _simulate(random.Random(5), 8000, theta=4.0, mu=0.5, sigma=0.3)
    fit = fit_ornstein_uhlenbeck(values, interval=DAY, maximum_condition=CONDITION)
    process, errors = fit.process.parameters, fit.process.standard_errors

    for name, truth in (("theta", 4.0), ("mu", 0.5), ("sigma", 0.3)):
        assert abs(process[name] - truth) < 3.0 * errors[name], name
    assert process["half_life"] == pytest.approx(math.log(2.0) / process["theta"], rel=1e-15)
    assert fit.observations == 7999 and fit.interval == DAY


def test_the_intervals_cover_the_truth_at_their_rate() -> None:
    """60 samples: the 95 percent intervals for mu and sigma cover within binomial error."""

    draws = random.Random(17)
    covered = {"mu": 0, "sigma": 0}
    samples = 60
    for _ in range(samples):
        fit = fit_ornstein_uhlenbeck(
            _simulate(draws, 4000, theta=6.0, mu=1.0, sigma=0.4),
            interval=DAY,
            maximum_condition=CONDITION,
        )
        for name, truth in (("mu", 1.0), ("sigma", 0.4)):
            estimate, error = fit.process.parameters[name], fit.process.standard_errors[name]
            covered[name] += abs(estimate - truth) <= 1.96 * error
    floor = 0.95 - 3.0 * math.sqrt(0.95 * 0.05 / samples)
    for name, count in covered.items():
        assert count / samples >= floor, (name, count)


def _dyadic(count: int) -> list[float]:
    state, value, values = 11, 0.0, []
    for _ in range(count):
        state = (1103515245 * state + 12345) % 2**31
        value = value / 2 + ((state >> 8) % 9 - 4) / 4
        values.append(value)
    return values


def test_every_value_is_the_exact_one() -> None:
    values = _dyadic(40)
    fit = fit_ornstein_uhlenbeck(values, interval=0.5, maximum_condition=CONDITION)

    x = [Fraction(v) for v in values[:-1]]
    y = [Fraction(v) for v in values[1:]]
    n = len(y)
    sx, sy = sum(x, Fraction(0)), sum(y, Fraction(0))
    sxx = sum((v * v for v in x), Fraction(0))
    sxy = sum((u * v for u, v in zip(x, y, strict=True)), Fraction(0))
    b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    a = (sy - b * sx) / n
    s2 = sum(((v - a - b * u) ** 2 for u, v in zip(x, y, strict=True)), Fraction(0)) / (n - 2)
    with localcontext() as context:
        context.prec = 50
        db, ds2 = Decimal(b.numerator) / b.denominator, Decimal(s2.numerator) / s2.denominator
        theta = -db.ln() / Decimal("0.5")
        mu = (Decimal(a.numerator) / a.denominator) / (1 - db)
        sigma = (2 * theta * ds2 / (1 - db * db)).sqrt()
        half_life = Decimal(2).ln() / theta

    assert fit.regression.parameters["coefficient"] == pytest.approx(float(b), rel=1e-13)
    assert fit.regression.parameters["intercept"] == pytest.approx(float(a), rel=1e-12)
    process = fit.process.parameters
    assert process["theta"] == pytest.approx(float(theta), rel=1e-13)
    assert process["mu"] == pytest.approx(float(mu), rel=1e-12)
    assert process["sigma"] == pytest.approx(float(sigma), rel=1e-13)
    assert process["half_life"] == pytest.approx(float(half_life), rel=1e-13)


def test_the_half_lifes_standard_error_is_the_coefficients_through_its_derivative() -> None:
    values = _simulate(random.Random(23), 3000, theta=5.0, mu=0.0, sigma=0.2)
    fit = fit_ornstein_uhlenbeck(values, interval=DAY, maximum_condition=CONDITION)
    b = fit.regression.parameters["coefficient"]
    se_b = fit.regression.standard_errors["coefficient"]

    def half_life(coefficient: float) -> float:
        return -DAY * math.log(2.0) / math.log(coefficient)

    step = 1e-7
    slope = (half_life(b + step) - half_life(b - step)) / (2 * step)
    assert fit.process.standard_errors["half_life"] == pytest.approx(abs(slope) * se_b, rel=1e-6)
    theta_slope = 1.0 / (DAY * b)
    assert fit.process.standard_errors["theta"] == pytest.approx(theta_slope * se_b, rel=1e-12)


def test_the_fit_is_identified_by_its_series_and_its_choices() -> None:
    values = _dyadic(60)
    fit = fit_ornstein_uhlenbeck(values, interval=0.25, maximum_condition=CONDITION)
    for estimate in (fit.regression, fit.process):
        assert estimate.data_id == series_identity(values)
        assert dict(estimate.settings) == {
            "interval": 0.25,
            "maximum_condition": CONDITION,
            "observations": 59,
        }
        assert estimate.numerical_class.identity_bearing
    other = fit_ornstein_uhlenbeck(values, interval=0.5, maximum_condition=CONDITION)
    assert other.process.estimate_id != fit.process.estimate_id
    assert other.regression.parameters == fit.regression.parameters


@pytest.mark.parametrize(
    ("values", "interval", "error", "match"),
    [
        ([1.02**t for t in range(60)], DAY, EconometricsInputError, "no mean reversion"),
        ([(-0.9) ** t + 0.01 * t for t in range(60)], DAY, EconometricsInputError, "outside"),
        (list(range(60)), 0.0, EconometricsInputError, "finite positive"),
        (list(range(60)), float("inf"), EconometricsInputError, "finite positive"),
        (list(range(60)), True, EconometricsInputError, "a number"),
        ([0.5] * 20, DAY, EconometricsInputError, "at least"),
        ([0.5, float("nan")] * 30, DAY, EconometricsInputError, "not finite"),
        ([1.0] * 60, DAY, IllConditionedError, "beyond the stated maximum"),
    ],
)
def test_what_the_fit_cannot_take_is_refused(
    values: list[float], interval: object, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        fit_ornstein_uhlenbeck(values, interval=interval, maximum_condition=CONDITION)  # type: ignore[arg-type]


def test_the_minimum_is_twenty_transitions() -> None:
    assert MINIMUM_ORNSTEIN_UHLENBECK_OBSERVATIONS == 20
    values = _dyadic(21)
    assert (
        fit_ornstein_uhlenbeck(values, interval=1.0, maximum_condition=CONDITION).observations == 20
    )
