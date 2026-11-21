"""The Ornstein-Uhlenbeck fit, and the half-life it implies (v4.3, ADR-0074 section 7, OFE-007).

A half-life is a property of a model, not of a series. Until v4.3 the factor
library refused to quote one: a fitted functional form is a model choice, and
the decay profile with its first negative horizon stayed the measurement
(ledger OFE-007). ADR-0074 reopened it as the estimate of a named model, with
its standard error, its identity and the model's name. The profile is unchanged.

The model
---------
``dX = θ (μ - X) dt + sigma dW``, observed every ``Δ``. Its exact discretisation is
an AR(1): ``X_{t+Δ} = μ (1 - b) + b X_t + ε``, with ``b = e^{-θΔ}`` and
``Var ε = sigma² (1 - b²) / (2θ)``. The fit is that regression, by least squares,
and the process's parameters are read off it:

* ``θ = -ln b / Δ``, the speed of reversion;
* ``μ = a / (1 - b)``, the level reverted to;
* ``sigma = sqrt(2θ s² / (1 - b²))``, from the residual variance ``s²``;
* the half-life ``ln 2 / θ``: the time the expected gap to ``μ`` takes to halve.

Each standard error is the delta method's, from the regression's covariance and
``Var s² = 2 s⁴ / (n - 2)``. A coefficient ``b`` outside ``(0, 1)`` measures no
mean reversion -- a unit root or worse, or an oscillation -- and is refused: no
half-life exists to report.

Classes
-------
The regression is class 2. Every logarithm is ``Decimal``'s, correctly rounded at
34 digits, and the rest is ``+ - * /`` and ``sqrt``, so the process's parameters
are class 2 too, the same bits on every host.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Final

from alphalab.common.estimate import Estimate, NumericalClass
from alphalab.econometrics._regression import ordinary_least_squares
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import require_series, series_identity

__all__ = [
    "MINIMUM_ORNSTEIN_UHLENBECK_OBSERVATIONS",
    "ORNSTEIN_UHLENBECK_METHOD",
    "ORNSTEIN_UHLENBECK_REGRESSION_METHOD",
    "OrnsteinUhlenbeckFit",
    "fit_ornstein_uhlenbeck",
]

#: The AR(1) regression's method scheme.
ORNSTEIN_UHLENBECK_REGRESSION_METHOD: Final = (
    "alphalab.econometrics.mean_reversion.ornstein_uhlenbeck/ar1-ols.v1"
)
#: The process's method scheme: the regression read through the exact discretisation.
ORNSTEIN_UHLENBECK_METHOD: Final = (
    "alphalab.econometrics.mean_reversion.ornstein_uhlenbeck/exact-discretisation.v1"
)
#: The fewest transitions a fit takes.
MINIMUM_ORNSTEIN_UHLENBECK_OBSERVATIONS: Final = 20


@dataclass(frozen=True, slots=True)
class OrnsteinUhlenbeckFit:
    """An Ornstein-Uhlenbeck fit: its regression and the process read off it.

    Attributes:
        regression: The AR(1) regression's estimate: ``intercept`` and
            ``coefficient`` (``a`` and ``b``), with their standard errors.
        process: ``theta``, ``mu``, ``sigma`` and ``half_life``, in the units
            of ``interval``, with their delta-method standard errors.
        interval: ``Δ``, the time between observations, as the caller stated it.
        observations: The transitions the regression is over.
    """

    regression: Estimate
    process: Estimate
    interval: float
    observations: int


def fit_ornstein_uhlenbeck(
    values: Sequence[float], *, interval: float, maximum_condition: float
) -> OrnsteinUhlenbeckFit:
    """Fit ``dX = θ (μ - X) dt + sigma dW`` to ``values`` observed every ``interval``.

    Args:
        values: The series, in time order, equally spaced.
        interval: The time between observations, in the unit the rates are
            wanted in: ``1 / 252`` for daily data read in years.
        maximum_condition: The largest condition number the regression may have.

    Raises:
        EconometricsInputError: If a value is not finite, the interval is not a
            finite positive number, fewer than 20 transitions are given, or the
            AR(1) coefficient is outside ``(0, 1)``, where no mean reversion is
            measured and no half-life exists.
        IllConditionedError: If the regression is too ill-conditioned: a series
            that does not move.
    """

    if isinstance(interval, bool) or not isinstance(interval, int | float):
        raise EconometricsInputError(f"interval is a number, got {interval!r}.")
    step = float(interval)
    if not (math.isfinite(step) and step > 0.0):
        raise EconometricsInputError(f"interval is a finite positive number, got {interval!r}.")
    series = require_series(values, MINIMUM_ORNSTEIN_UHLENBECK_OBSERVATIONS + 1, "The series")
    previous, following = series[:-1], series[1:]
    count = len(following)
    fit = ordinary_least_squares([[1.0, x] for x in previous], following, maximum_condition)
    a, b = fit.coefficients
    if not 0.0 < b < 1.0:
        raise EconometricsInputError(
            f"The AR(1) coefficient is {b!r}: outside (0, 1) the series measures no mean "
            "reversion -- a unit root, an explosion or an oscillation -- so there is no "
            "half-life to report."
        )
    s2 = fit.variance
    # The coefficients' covariance, s² (X'X)⁻¹, for the two-column design.
    total = math.fsum(previous)
    squares = math.fsum(x * x for x in previous)
    determinant = count * squares - total * total
    var_a = s2 * squares / determinant
    var_b = s2 * count / determinant
    cov_ab = -s2 * total / determinant
    var_s2 = 2.0 * s2 * s2 / (count - 2)

    log_b = _log(b)
    theta = -log_b / step
    mu = a / (1.0 - b)
    complement = 1.0 - b * b
    sigma2 = 2.0 * theta * s2 / complement
    sigma = math.sqrt(sigma2)
    half_life = _log(2.0) / theta

    # Delta-method standard errors.
    d_theta_b = -1.0 / (step * b)
    se_theta = abs(d_theta_b) * math.sqrt(var_b)
    d_mu_a = 1.0 / (1.0 - b)
    d_mu_b = a / ((1.0 - b) * (1.0 - b))
    se_mu = math.sqrt(
        max(d_mu_a * d_mu_a * var_a + 2.0 * d_mu_a * d_mu_b * cov_ab + d_mu_b * d_mu_b * var_b, 0.0)
    )
    d_half_life_b = step * _log(2.0) / (b * log_b * log_b)
    se_half_life = abs(d_half_life_b) * math.sqrt(var_b)
    d_sigma2_b = 2.0 * s2 * (d_theta_b * complement + 2.0 * b * theta) / (complement * complement)
    d_sigma2_s2 = 2.0 * theta / complement
    se_sigma = math.sqrt(d_sigma2_b * d_sigma2_b * var_b + d_sigma2_s2 * d_sigma2_s2 * var_s2) / (
        2.0 * sigma
    )

    data_id = series_identity(series)
    settings = {
        "interval": step,
        "maximum_condition": float(maximum_condition),
        "observations": count,
    }
    regression = Estimate(
        method=ORNSTEIN_UHLENBECK_REGRESSION_METHOD,
        data_id=data_id,
        parameters={"intercept": a, "coefficient": b},
        standard_errors={
            "intercept": fit.standard_errors[0],
            "coefficient": fit.standard_errors[1],
        },
        settings=settings,
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
    )
    process = Estimate(
        method=ORNSTEIN_UHLENBECK_METHOD,
        data_id=data_id,
        parameters={"theta": theta, "mu": mu, "sigma": sigma, "half_life": half_life},
        standard_errors={
            "theta": se_theta,
            "mu": se_mu,
            "sigma": se_sigma,
            "half_life": se_half_life,
        },
        settings=settings,
        numerical_class=NumericalClass.CORRECTLY_ROUNDED,
    )
    return OrnsteinUhlenbeckFit(regression, process, step, count)


def _log(value: float) -> float:
    """``ln value``, correctly rounded at 34 digits and rounded once to a float."""

    with localcontext() as context:
        context.prec = 34
        return float(Decimal(value).ln())
