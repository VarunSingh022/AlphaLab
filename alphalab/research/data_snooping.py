"""Tests that control for the search: White's Reality Check and Hansen's SPA (v4.3, ADR-0054).

Given ``K`` models' performance relative to a benchmark over ``T`` periods, both
ask whether the best of them beats the benchmark, given that all ``K`` were
tried. The null hypothesis is that none does: every model's expected relative
performance is at most zero.

* **White's Reality Check** (2000) bootstraps the distribution of the largest
  mean under the least favourable null, every model's mean zero. Its p-value
  can be driven up by adding poor models, which inflate the bootstrap maximum.
* **Hansen's test of superior predictive ability** (2005) studentizes each mean
  by its bootstrap standard deviation and recenters the bootstrap under the
  null three ways, reporting all three p-values: *lower*, which takes a model's
  negative mean at face value; *consistent*, which does so only below
  ``-s sqrt(2 ln ln T)``, ``s`` the bootstrap deviation of its mean; and
  *upper*, the least favourable case, the studentized Reality Check. So
  ``p_lower <= p_consistent <= p_upper`` always, and a model far below the
  benchmark leaves the consistent p-value as it was.

Both resample periods by the stationary bootstrap
(:func:`~alphalab.research.perturbation.stationary_bootstrap_indices`), so the
series' dependence is kept on average up to the mean block length. Both assume
the relative performance is stationary and weakly dependent; a regime change
inside the sample violates that and nothing here detects it (LIM-011).

Numerical classes (ADR-0058)
----------------------------
The bootstrap positions are class 1. Means, standard deviations and statistics
are ``fsum``, ``sqrt`` and ``+ - * /``, class 2. The consistent threshold's
``sqrt(2 ln ln T / T)`` is ``Decimal``'s correctly rounded logarithm and square
root, so it is class 2 as well. A p-value is a ratio of counts, held exactly as
a :class:`~fractions.Fraction`: ``#{bootstrap statistic >= statistic} / B``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction

from alphalab.common.estimate import NumericalClass
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.perturbation import stationary_bootstrap_indices
from alphalab.research.selection import TrialMatrix

__all__ = [
    "RealityCheckResult",
    "SPAResult",
    "reality_check",
    "superior_predictive_ability",
]


@dataclass(frozen=True, slots=True)
class RealityCheckResult:
    """White's Reality Check: does the best of ``K`` models beat the benchmark?

    Attributes:
        statistic: ``V = max_k sqrt(T) mean_k``.
        p_value: ``#{V*_b >= V} / B``, exactly.
        best: The model with the largest mean, the first of equal ones.
        replications: ``B``.
        mean_block_length: The stationary bootstrap's ``q``.
        seed: The bootstrap's seed.
        numerical_class: Correctly rounded (class 2); the p-value is exact.
    """

    statistic: float
    p_value: Fraction
    best: str
    replications: int
    mean_block_length: float
    seed: int
    numerical_class: NumericalClass = NumericalClass.CORRECTLY_ROUNDED


@dataclass(frozen=True, slots=True)
class SPAResult:
    """Hansen's test of superior predictive ability, under its three recenterings.

    Attributes:
        statistic: ``T_SPA = max(0, max_k sqrt(T) mean_k / sd_k)``, ``sd_k`` the
            bootstrap standard deviation of ``sqrt(T) mean_k``.
        p_lower: The p-value recentering each mean by ``max(mean, 0)``.
        p_consistent: Recentering by the mean where it is at least
            ``-sd_k sqrt(2 ln ln T / T)``, by nothing below.
        p_upper: Recentering by the mean itself: the least favourable case.
        best: The model with the largest studentized mean, the first of equals.
        replications: ``B``.
        mean_block_length: The stationary bootstrap's ``q``.
        seed: The bootstrap's seed.
        numerical_class: Correctly rounded (class 2); the p-values are exact.
    """

    statistic: float
    p_lower: Fraction
    p_consistent: Fraction
    p_upper: Fraction
    best: str
    replications: int
    mean_block_length: float
    seed: int
    numerical_class: NumericalClass = NumericalClass.CORRECTLY_ROUNDED


def _bootstrap_means(
    matrix: TrialMatrix, replications: int, mean_block_length: float, seed: int
) -> tuple[list[float], list[list[float]]]:
    """Each model's mean, and its mean in every bootstrap sample (``B`` rows of ``K``)."""

    periods = matrix.periods
    means = [math.fsum(column) / periods for column in matrix.columns]
    samples = stationary_bootstrap_indices(periods, mean_block_length, replications, seed)
    resampled = [
        [math.fsum(column[p] for p in positions) / periods for column in matrix.columns]
        for positions in samples
    ]
    return means, resampled


def reality_check(
    relative: TrialMatrix,
    *,
    replications: int,
    mean_block_length: float,
    seed: int,
) -> RealityCheckResult:
    """White's (2000) Reality Check for data snooping.

    Args:
        relative: ``T`` periods by ``K`` models of each model's performance
            relative to the benchmark: positive where the model did better.
        replications: ``B``, the bootstrap samples. Required.
        mean_block_length: The stationary bootstrap's ``q``, at least 1.
            Required: it is the dependence the bootstrap keeps.
        seed: The bootstrap's seed. Required.

    Raises:
        ResearchValidationError: If an argument is out of range.
    """

    means, resampled = _bootstrap_means(relative, replications, mean_block_length, seed)
    root = math.sqrt(relative.periods)
    statistic = max(root * value for value in means)
    best = max(range(relative.trials), key=lambda k: means[k])
    exceed = sum(
        1
        for row in resampled
        if max(root * (b - m) for b, m in zip(row, means, strict=True)) >= statistic
    )
    return RealityCheckResult(
        statistic=statistic,
        p_value=Fraction(exceed, replications),
        best=relative.labels[best],
        replications=replications,
        mean_block_length=float(mean_block_length),
        seed=seed,
    )


def _consistency_factor(periods: int) -> float:
    """``sqrt(2 ln ln T / T)``, by ``Decimal``'s correctly rounded functions."""

    with localcontext() as context:
        context.prec = 34
        return float((2 * Decimal(periods).ln().ln() / periods).sqrt())


def superior_predictive_ability(
    relative: TrialMatrix,
    *,
    replications: int,
    mean_block_length: float,
    seed: int,
) -> SPAResult:
    """Hansen's (2005) test of superior predictive ability.

    Args:
        relative: As for :func:`reality_check`, over at least 3 periods, since
            the consistent threshold takes ``ln ln T``.
        replications: ``B``, at least 2, since each model's standard deviation is
            estimated from the bootstrap. Required.
        mean_block_length: The stationary bootstrap's ``q``, at least 1. Required.
        seed: The bootstrap's seed. Required.

    Raises:
        ResearchValidationError: If an argument is out of range, or a model's
            bootstrap standard deviation is zero: its relative performance is
            constant, and a studentized mean of it is undefined.
    """

    if relative.periods < 3:
        raise ResearchValidationError(
            f"SPA's consistent threshold takes ln ln T, which needs at least 3 periods; the "
            f"matrix has {relative.periods}."
        )
    if isinstance(replications, int) and not isinstance(replications, bool) and replications < 2:
        raise ResearchValidationError(
            f"SPA estimates each model's standard deviation from the bootstrap, so it needs at "
            f"least 2 replications, got {replications!r}."
        )
    means, resampled = _bootstrap_means(relative, replications, mean_block_length, seed)
    root = math.sqrt(relative.periods)
    models = range(relative.trials)
    deviations = []
    for k in models:
        squares = math.fsum(
            (root * (row[k] - means[k])) * (root * (row[k] - means[k])) for row in resampled
        )
        deviation = math.sqrt(squares / replications)
        if deviation == 0.0:
            raise ResearchValidationError(
                f"Model {relative.labels[k]!r} has a bootstrap standard deviation of zero: its "
                "relative performance does not vary, so its studentized mean is undefined."
            )
        deviations.append(deviation)
    studentized = [root * means[k] / deviations[k] for k in models]
    statistic = max(0.0, max(studentized))
    best = max(models, key=lambda k: studentized[k])
    factor = _consistency_factor(relative.periods)
    centres = {
        "lower": [max(m, 0.0) for m in means],
        "consistent": [m if m >= -deviations[k] * factor else 0.0 for k, m in enumerate(means)],
        "upper": list(means),
    }
    counts = dict.fromkeys(centres, 0)
    for row in resampled:
        for name, centre in centres.items():
            simulated = max(
                0.0,
                max(root * (row[k] - centre[k]) / deviations[k] for k in models),
            )
            if simulated >= statistic:
                counts[name] += 1
    return SPAResult(
        statistic=statistic,
        p_lower=Fraction(counts["lower"], replications),
        p_consistent=Fraction(counts["consistent"], replications),
        p_upper=Fraction(counts["upper"], replications),
        best=relative.labels[best],
        replications=replications,
        mean_block_length=float(mean_block_length),
        seed=seed,
    )
