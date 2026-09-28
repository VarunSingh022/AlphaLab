"""How much a Sharpe ratio can be believed: the probabilistic and deflated ratios.

A Sharpe ratio is an estimate, and the same figure means different things over
twenty observations and over two thousand, over symmetric returns and over
returns that earn a little most days and lose a lot on a few. It also means
something different when it is the best of a hundred configurations tried.
This module answers both (ledger OFE-005), after Bailey and Lopez de Prado.

The probabilistic Sharpe ratio (PSR)
------------------------------------
The probability that the true Sharpe ratio exceeds a benchmark ``SR*``, given
the estimate ``SR`` over ``n`` returns with skewness ``g3`` and kurtosis
``g4``::

    PSR(SR*) = Phi( (SR - SR*) sqrt(n - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2) )

(Bailey and Lopez de Prado, "The Sharpe Ratio Efficient Frontier", 2012.) The
skewness and kurtosis terms are what make it more than a normal-theory
interval: negative skew and fat tails widen the estimate's standard error, and
a strategy selling tail risk is charged for it.

The deflated Sharpe ratio (DSR)
-------------------------------
The PSR against the Sharpe ratio the *best* of ``N`` independent trials would
be expected to show by luck alone, when every one of them is worthless::

    SR*_0 = sqrt(V) ( (1 - gamma) Phi^-1(1 - 1/N) + gamma Phi^-1(1 - 1/(N e)) )

where ``V`` is the variance of the trials' Sharpe ratios and ``gamma`` the
Euler-Mascheroni constant (Bailey and Lopez de Prado, "The Deflated Sharpe
Ratio", 2014). A DSR above 0.95 says the selected result is unlikely to be the
best of ``N`` pieces of noise.

Assumptions, stated
-------------------
* **Independent, identically distributed returns.** The adjustment is for the
  shape of the distribution, not for serial correlation; an autocorrelated
  series has a different standard error, and neither ratio corrects for it.
* **Per-period ratios.** ``SR`` and every trial's Sharpe ratio are the plain,
  *un-annualized* ``mean / sample standard deviation`` of returns at one
  frequency. Annualizing multiplies the ratio by ``sqrt(periods)`` and ``n``
  does not change with it, so an annualized figure here would be wrong by that
  factor. :func:`per_period_sharpe` computes the figure the formulas expect.
* **Independent trials.** ``N`` is the number of *effectively independent*
  configurations. A sweep of neighbouring windows is far fewer independent
  trials than configurations; the caller states the number, and using the raw
  count overstates the expected maximum, which deflates more rather than less.
* **Moments.** Skewness and kurtosis are
  :func:`~alphalab.common.statistics.standardized_moments` -- averaged over
  ``n``, standardized by the ``n - 1`` deviation -- and the kurtosis is *not*
  excess: a normal distribution has 3.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import (
    mean,
    normal_cdf,
    normal_quantile,
    sample_variance,
    standardized_moments,
)
from alphalab.research.exceptions import ResearchValidationError

__all__ = [
    "EULER_MASCHERONI",
    "DeflatedSharpe",
    "SharpeInference",
    "deflated_sharpe_ratio",
    "expected_maximum_sharpe",
    "per_period_sharpe",
    "probabilistic_sharpe_ratio",
]

#: The Euler-Mascheroni constant, to double precision.
EULER_MASCHERONI: Final = 0.5772156649015329

#: Fewer observations than this leave the third and fourth moments -- which
#: the standard error depends on -- meaningless. The same floor the
#: Cornish-Fisher VaR applies for the same reason.
MINIMUM_OBSERVATIONS: Final = 30


@dataclass(frozen=True, slots=True)
class SharpeInference:
    """A Sharpe ratio, its sampling uncertainty, and the probability it beats a bar.

    Attributes:
        observations: ``n``, the returns the estimate rests on.
        sharpe: The per-period Sharpe ratio ``mean / sample standard deviation``.
        skewness: The returns' skewness.
        kurtosis: Their kurtosis (a normal distribution has 3).
        benchmark_sharpe: ``SR*``, the per-period ratio being tested against.
        standard_error: The estimate's standard error under the stated
            assumptions, ``sqrt((1 - g3 SR + (g4 - 1)/4 SR^2) / (n - 1))``.
        probabilistic_sharpe: ``PSR(SR*)``, in ``[0, 1]``.
    """

    observations: int
    sharpe: float
    skewness: float
    kurtosis: float
    benchmark_sharpe: float
    standard_error: float
    probabilistic_sharpe: float

    @property
    def p_value(self) -> float:
        """``1 - PSR``: the one-sided p-value of ``SR <= SR*``.

        What :func:`~alphalab.research.multiple_testing.correct_p_values`
        takes, when several strategies are tested at once.
        """

        return 1.0 - self.probabilistic_sharpe


@dataclass(frozen=True, slots=True)
class DeflatedSharpe:
    """The selected result's PSR against the expected maximum of ``N`` null trials.

    Attributes:
        inference: The PSR computed at :attr:`expected_maximum`.
        independent_trials: ``N`` as stated by the caller.
        trial_sharpe_variance: ``V``, the variance of the trials' per-period
            Sharpe ratios.
        expected_maximum: ``SR*_0``, the per-period Sharpe ratio the best of
            ``N`` worthless trials would be expected to show.
    """

    inference: SharpeInference
    independent_trials: int
    trial_sharpe_variance: float
    expected_maximum: float

    @property
    def deflated_sharpe(self) -> float:
        """The DSR: ``PSR(SR*_0)``."""

        return self.inference.probabilistic_sharpe


def _require_returns(returns: Sequence[float]) -> tuple[float, ...]:
    series = tuple(returns)
    if len(series) < MINIMUM_OBSERVATIONS:
        raise ResearchValidationError(
            f"A Sharpe inference needs at least {MINIMUM_OBSERVATIONS} returns for their "
            f"skewness and kurtosis to mean anything; {len(series)} were given."
        )
    return series


def per_period_sharpe(returns: Sequence[float]) -> float:
    """``mean / sample standard deviation``: the un-annualized Sharpe ratio.

    Of excess returns, if the benchmark rate matters: subtract it first.

    Raises:
        ResearchValidationError: If fewer than two returns are given, any is
            not finite, or they have no dispersion -- the ratio is undefined,
            not zero or infinite.
    """

    series = tuple(returns)
    try:
        deviation = math.sqrt(sample_variance(series))
        average = mean(series)
    except AlphaLabValidationError as error:
        raise ResearchValidationError(f"A Sharpe ratio is undefined here: {error}") from None
    if deviation == 0.0:
        raise ResearchValidationError(
            "A Sharpe ratio is undefined for returns with no dispersion: it divides by zero."
        )
    return average / deviation


def probabilistic_sharpe_ratio(
    returns: Sequence[float], benchmark_sharpe: float
) -> SharpeInference:
    """The probability that the true per-period Sharpe ratio exceeds ``benchmark_sharpe``.

    Args:
        returns: The (excess) returns, at one frequency, in time order.
        benchmark_sharpe: ``SR*``, per period, at the same frequency. ``0.0``
            asks whether the strategy has any skill at all.

    Raises:
        ResearchValidationError: If fewer than :data:`MINIMUM_OBSERVATIONS`
            returns are given, any is not finite, the returns are constant,
            ``benchmark_sharpe`` is not finite, or the returns take exactly two
            values in a proportion that leaves the standard error zero.
    """

    series = _require_returns(returns)
    if not math.isfinite(benchmark_sharpe):
        raise ResearchValidationError(
            f"benchmark_sharpe must be a finite number, got {benchmark_sharpe!r}."
        )
    sharpe = per_period_sharpe(series)
    moments = standardized_moments(series)
    count = len(series)
    spread = 1.0 - moments.skewness * sharpe + (moments.kurtosis - 1.0) / 4.0 * sharpe * sharpe
    if not spread > 0.0:
        raise ResearchValidationError(
            f"The Sharpe ratio's standard error is zero for these returns (skewness "
            f"{moments.skewness!r}, kurtosis {moments.kurtosis!r}): a two-point distribution "
            "at exactly the proportion that cancels it. No probability can be read from it."
        )
    standard_error = math.sqrt(spread / (count - 1))
    probability = normal_cdf((sharpe - benchmark_sharpe) / standard_error)
    return SharpeInference(
        observations=count,
        sharpe=sharpe,
        skewness=moments.skewness,
        kurtosis=moments.kurtosis,
        benchmark_sharpe=benchmark_sharpe,
        standard_error=standard_error,
        probabilistic_sharpe=probability,
    )


def expected_maximum_sharpe(independent_trials: int, trial_sharpe_variance: float) -> float:
    """``SR*_0``: the Sharpe ratio the best of ``N`` null trials would show by luck.

    Raises:
        ResearchValidationError: If ``independent_trials`` is below 2 -- the
            maximum of one trial is that trial, and the expression's first
            quantile is infinite -- or the variance is negative or not finite.
    """

    if isinstance(independent_trials, bool) or not isinstance(independent_trials, int):
        raise ResearchValidationError(
            f"independent_trials must be an integer, got {independent_trials!r}."
        )
    if independent_trials < 2:
        raise ResearchValidationError(
            f"The expected maximum of {independent_trials} trial(s) is undefined here: "
            "deflation answers 'the best of several', and one trial was not selected from "
            "anything. Use probabilistic_sharpe_ratio against a stated benchmark."
        )
    if not math.isfinite(trial_sharpe_variance) or trial_sharpe_variance < 0.0:
        raise ResearchValidationError(
            f"trial_sharpe_variance must be a finite non-negative number, got "
            f"{trial_sharpe_variance!r}."
        )
    first = normal_quantile(1.0 - 1.0 / independent_trials)
    second = normal_quantile(1.0 - 1.0 / (independent_trials * math.e))
    return math.sqrt(trial_sharpe_variance) * (
        (1.0 - EULER_MASCHERONI) * first + EULER_MASCHERONI * second
    )


def deflated_sharpe_ratio(
    returns: Sequence[float],
    trial_sharpes: Sequence[float],
    *,
    independent_trials: int,
) -> DeflatedSharpe:
    """The selected strategy's PSR against the expected maximum of the search.

    Args:
        returns: The selected configuration's (excess) returns.
        trial_sharpes: The per-period Sharpe ratio of *every* configuration the
            search evaluated, the selected one included -- their variance is
            ``V``.
        independent_trials: ``N``, the effective number of independent trials,
            at most ``len(trial_sharpes)``. Required: the caller knows how
            correlated the configurations were, and the count of them is not
            the answer.

    Raises:
        ResearchValidationError: If fewer than two trial Sharpe ratios are
            given, one is not finite, ``independent_trials`` exceeds their
            number, or as :func:`probabilistic_sharpe_ratio` and
            :func:`expected_maximum_sharpe`.
    """

    trials = tuple(trial_sharpes)
    if len(trials) < 2:
        raise ResearchValidationError(
            f"Deflation needs the Sharpe ratio of every trial, and at least 2; got {len(trials)}."
        )
    for index, value in enumerate(trials):
        if not math.isfinite(value):
            raise ResearchValidationError(f"trial_sharpes[{index}] is {value!r}.")
    if isinstance(independent_trials, int) and independent_trials > len(trials):
        raise ResearchValidationError(
            f"independent_trials is {independent_trials} but only {len(trials)} trial Sharpe "
            "ratios were given; the effective number of trials cannot exceed the number run."
        )
    variance = sample_variance(trials)
    threshold = expected_maximum_sharpe(independent_trials, variance)
    return DeflatedSharpe(
        inference=probabilistic_sharpe_ratio(returns, threshold),
        independent_trials=independent_trials,
        trial_sharpe_variance=variance,
        expected_maximum=threshold,
    )
