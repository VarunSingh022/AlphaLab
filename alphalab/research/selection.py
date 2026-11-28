"""Selection statistics over a search: PBO, MinTRL and the adjusted PSR (v4.3, ADR-0054).

A researcher who tries many configurations and keeps the best is measuring the
search as much as the strategy. These functions measure the search, purely: they
compute over the numbers they are given, import nothing but ``common`` and
their own package, and decide nothing. No threshold says "overfit" or
"significant"; a result is a number with its inputs and assumptions, and the
policy that judges it is the caller's (ADR-0054 section 5).

:class:`TrialMatrix` is the population: ``T`` periods by ``N`` trials of one
per-period measure over one common set of instants. Which trials belong in it is
decided by whoever assembles it, and never assumed here.

Numerical classes (ADR-0058)
----------------------------
* :func:`probability_of_backtest_overfitting` is exact where it counts: PBO is a
  ratio of integer counts, held as a :class:`~fractions.Fraction`, and whether a
  selection overfits is an integer comparison of ranks. The performances it
  ranks are correctly rounded (class 2), and so are the logits, which come from
  ``Decimal``'s correctly rounded logarithm.
* :func:`minimum_track_record_length` and
  :func:`autocorrelation_adjusted_probabilistic_sharpe_ratio` go through the
  normal distribution's ``erf`` and its inverse, so they are class 3: compared by
  tolerance, never in an identity.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from fractions import Fraction
from typing import Final

from alphalab.common.estimate import NumericalClass
from alphalab.common.statistics import (
    mean,
    normal_cdf,
    normal_quantile,
    sample_variance,
    standardized_moments,
)
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.sharpe_inference import (
    MINIMUM_OBSERVATIONS,
    SharpeInference,
    per_period_sharpe,
)
from alphalab.research.splits import require_chronological

__all__ = [
    "MAXIMUM_BLOCKS",
    "AdjustedSharpeInference",
    "MinTRLResult",
    "PBOResult",
    "SelectionMeasure",
    "TrialMatrix",
    "Trim",
    "autocorrelation_adjusted_probabilistic_sharpe_ratio",
    "minimum_track_record_length",
    "probability_of_backtest_overfitting",
]

#: The most blocks CSCV takes: ``C(20, 10)`` is 184,756 combinations, each ranking
#: every trial twice, which is as far as pure Python goes in minutes (R54-8).
MAXIMUM_BLOCKS: Final = 20


# --------------------------------------------------------------------------- #
# The trial matrix
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TrialMatrix:
    """``T`` periods by ``N`` trials of one per-period measure, over one set of instants.

    Attributes:
        labels: Each trial's label, in column order; distinct.
        instants: The ``T`` instants every column is measured at, strictly
            ascending.
        columns: One column of ``T`` finite values per trial, in label order.
        measure: What the values are: ``"daily excess return"``, ``"monthly
            loss differential to the benchmark"``.

    Raises:
        ResearchValidationError: If there are fewer than two trials, a label
            repeats or is empty, the instants are not strictly ascending, a
            column's length differs from the instants', a value is not finite,
            or the measure is not named.
    """

    labels: tuple[str, ...]
    instants: tuple[float, ...]
    columns: tuple[tuple[float, ...], ...]
    measure: str

    def __post_init__(self) -> None:
        labels = tuple(self.labels)
        if len(labels) < 2:
            raise ResearchValidationError(
                f"A trial matrix needs at least two trials to select among; it has {len(labels)}."
            )
        for label in labels:
            if not isinstance(label, str) or not label.strip():
                raise ResearchValidationError(f"A trial's label must be a name, got {label!r}.")
        if len(set(labels)) != len(labels):
            repeated = sorted({label for label in labels if labels.count(label) > 1})
            raise ResearchValidationError(f"Trial labels repeat: {repeated}.")
        instants = require_chronological(tuple(float(value) for value in self.instants))
        if len(self.columns) != len(labels):
            raise ResearchValidationError(
                f"{len(labels)} labels and {len(self.columns)} columns: each trial needs one."
            )
        columns = []
        for label, column in zip(labels, self.columns, strict=True):
            values = tuple(float(value) for value in column)
            if len(values) != len(instants):
                raise ResearchValidationError(
                    f"Trial {label!r} has {len(values)} values over {len(instants)} instants; "
                    "a trial matrix is over one common set of instants."
                )
            for index, value in enumerate(values):
                if not math.isfinite(value):
                    raise ResearchValidationError(
                        f"Trial {label!r} holds {value!r} at period {index}; a missing value "
                        "is the assembler's to resolve, not this matrix's."
                    )
            columns.append(values)
        if not isinstance(self.measure, str) or not self.measure.strip():
            raise ResearchValidationError("A trial matrix names the measure its values are.")
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "instants", instants)
        object.__setattr__(self, "columns", tuple(columns))

    @classmethod
    def from_columns(
        cls, columns: Mapping[str, Sequence[float]], instants: Sequence[float], measure: str
    ) -> TrialMatrix:
        """A matrix from columns keyed by label, in the mapping's order."""

        return cls(
            labels=tuple(columns),
            instants=tuple(instants),
            columns=tuple(tuple(column) for column in columns.values()),
            measure=measure,
        )

    @property
    def periods(self) -> int:
        """``T``."""

        return len(self.instants)

    @property
    def trials(self) -> int:
        """``N``."""

        return len(self.labels)

    def column(self, label: str) -> tuple[float, ...]:
        """The column of the trial labelled ``label``."""

        try:
            return self.columns[self.labels.index(label)]
        except ValueError:
            raise ResearchValidationError(f"No trial is labelled {label!r}.") from None


# --------------------------------------------------------------------------- #
# PBO by combinatorially symmetric cross-validation
# --------------------------------------------------------------------------- #


class SelectionMeasure(Enum):
    """How a trial's performance over a set of periods is measured, to select and to rank."""

    #: The mean of the values, compared by their exactly rounded sum, which
    #: orders trials as the mean does over one common set of periods.
    MEAN = "mean"
    #: The per-period Sharpe ratio: the mean over the sample standard deviation.
    #: A trial with no dispersion has 0 for a zero mean and an infinity of the
    #: mean's sign otherwise.
    SHARPE_RATIO = "sharpe_ratio"


class Trim(Enum):
    """What CSCV does with the ``T mod S`` periods that do not fill a block."""

    #: Nothing: ``T`` must be a multiple of ``S``.
    NONE = "none"
    #: Drop the oldest periods.
    OLDEST = "oldest"
    #: Drop the newest periods.
    NEWEST = "newest"


@dataclass(frozen=True, slots=True)
class PBOResult:
    """The probability of backtest overfitting, and how it was counted.

    Attributes:
        probability: PBO: the fraction of the ``C(S, S/2)`` splits in which the
            configuration best in sample ranks at or below the median out of
            sample. Exact.
        combinations: ``C(S, S/2)``, the splits counted.
        blocks: ``S``.
        block_length: Periods per block.
        trimmed: Periods dropped to make ``T`` a multiple of ``S``.
        trim: Which end they were dropped from.
        measure: The selection measure.
        selected: The in-sample best trial of each split, in the splits' order
            (lexicographic in the in-sample blocks).
        logits: ``ln(w / (1 - w))`` for each split, ``w`` the selected trial's
            out-of-sample midrank over ``N + 1``; ``<= 0`` is what PBO counts.
        convention: How ties are decided, as stated.
        numerical_class: Correctly rounded (class 2); PBO itself is exact.
    """

    probability: Fraction
    combinations: int
    blocks: int
    block_length: int
    trimmed: int
    trim: Trim
    measure: SelectionMeasure
    selected: tuple[str, ...]
    logits: tuple[float, ...]
    convention: str
    numerical_class: NumericalClass = NumericalClass.CORRECTLY_ROUNDED


_CONVENTION: Final = (
    "the in-sample best is the first trial of equal performance; out-of-sample ranks are "
    "midranks, 1 the worst; a split counts toward PBO when the logit is at most 0, the "
    "selected trial at or below the median, as Bailey et al. integrate up to and including 0"
)


def _block_statistics(
    column: tuple[float, ...], blocks: int, length: int, start: int
) -> list[tuple[float, float, float]]:
    """Each block's exactly rounded sum, its mean, and its sum of squared deviations."""

    statistics = []
    for block in range(blocks):
        values = column[start + block * length : start + (block + 1) * length]
        total = math.fsum(values)
        centre = total / length
        statistics.append((total, centre, math.fsum((v - centre) * (v - centre) for v in values)))
    return statistics


def _performance(
    statistics: list[tuple[float, float, float]],
    chosen: Sequence[int],
    length: int,
    measure: SelectionMeasure,
) -> float:
    """The measure over the chosen blocks.

    The mean is compared by the sum, which orders trials as the mean does over
    one common set of periods and keeps an exact tie exact where a division would
    round it apart. The Sharpe ratio's variance is Chan, Golub and LeVeque's
    pairwise combination of the blocks' sums of squares.
    """

    total = math.fsum(statistics[b][0] for b in chosen)
    if measure is SelectionMeasure.MEAN:
        return total
    count = length * len(chosen)
    centre = total / count
    spread = math.fsum(
        [
            *(statistics[b][2] for b in chosen),
            *(length * (statistics[b][1] - centre) * (statistics[b][1] - centre) for b in chosen),
        ]
    )
    variance = spread / (count - 1)
    if variance == 0.0:
        return 0.0 if centre == 0.0 else math.copysign(math.inf, centre)
    return centre / math.sqrt(variance)


def _logits(trials: int) -> dict[int, float]:
    """The logit for each doubled midrank ``d`` in ``2 .. 2N``: ``ln(d) - ln(2N + 2 - d)``."""

    with localcontext() as context:
        context.prec = 34
        return {
            doubled: float(Decimal(doubled).ln() - Decimal(2 * trials + 2 - doubled).ln())
            for doubled in range(2, 2 * trials + 1)
        }


def probability_of_backtest_overfitting(
    matrix: TrialMatrix,
    *,
    blocks: int,
    measure: SelectionMeasure,
    trim: Trim,
) -> PBOResult:
    """PBO by combinatorially symmetric cross-validation (Bailey, Borwein, López de Prado and Zhu).

    The ``T`` periods are cut into ``S`` contiguous blocks of equal length. For
    every way of choosing half of them as the in-sample set, the rest out of
    sample, the trial best in sample is selected and ranked among all ``N``
    out of sample. PBO is the fraction of the ``C(S, S/2)`` splits in which it
    ranks at or below the median: a property of the selection procedure, not
    of any strategy. Blocks are exchangeable here, and dependence across a
    block boundary is ignored (LIM-011).

    Args:
        matrix: The population of trials.
        blocks: ``S``, even, with ``2 <= S <= T`` and ``S <= 20``. Required.
        measure: How performance is measured, in and out of sample. Required.
        trim: Which periods to drop, if ``T`` is not a multiple of ``S``.
            Required, so that dropping data is a choice the result records.

    Raises:
        ResearchValidationError: If ``blocks`` is out of range, ``T`` is not a
            multiple of ``S`` under :attr:`Trim.NONE`, or the Sharpe ratio is
            asked of a half with fewer than two periods.
    """

    if isinstance(blocks, bool) or not isinstance(blocks, int):
        raise ResearchValidationError(f"blocks must be an integer, got {blocks!r}.")
    if blocks < 2 or blocks % 2 or blocks > MAXIMUM_BLOCKS or blocks > matrix.periods:
        raise ResearchValidationError(
            f"blocks must be even, at least 2, at most {MAXIMUM_BLOCKS} and at most the "
            f"{matrix.periods} periods; got {blocks!r}."
        )
    if not isinstance(measure, SelectionMeasure) or not isinstance(trim, Trim):
        raise ResearchValidationError("measure and trim must be a SelectionMeasure and a Trim.")
    leftover = matrix.periods % blocks
    if leftover and trim is Trim.NONE:
        raise ResearchValidationError(
            f"{matrix.periods} periods do not fill {blocks} equal blocks: {leftover} are left "
            "over. Choose Trim.OLDEST or Trim.NEWEST to drop them; dropping data is not done "
            "silently."
        )
    length = matrix.periods // blocks
    half = blocks // 2
    if measure is SelectionMeasure.SHARPE_RATIO and length * half < 2:
        raise ResearchValidationError(
            "A Sharpe ratio needs at least two periods on each side of a split; these "
            f"blocks give {length * half}."
        )
    start = leftover if trim is Trim.OLDEST else 0
    statistics = [_block_statistics(column, blocks, length, start) for column in matrix.columns]
    logit_of = _logits(matrix.trials)
    trials = range(matrix.trials)
    overfit = 0
    selected: list[str] = []
    logits: list[float] = []
    for inside in itertools.combinations(range(blocks), half):
        outside = [block for block in range(blocks) if block not in inside]
        in_sample = [_performance(s, inside, length, measure) for s in statistics]
        out_of_sample = [_performance(s, outside, length, measure) for s in statistics]
        best = max(trials, key=lambda trial: in_sample[trial])
        level = out_of_sample[best]
        below = sum(1 for value in out_of_sample if value < level)
        equal = sum(1 for value in out_of_sample if value == level)
        doubled = 2 * below + equal + 1
        if doubled <= matrix.trials + 1:
            overfit += 1
        selected.append(matrix.labels[best])
        logits.append(logit_of[doubled])
    combinations = len(logits)
    return PBOResult(
        probability=Fraction(overfit, combinations),
        combinations=combinations,
        blocks=blocks,
        block_length=length,
        trimmed=leftover,
        trim=trim,
        measure=measure,
        selected=tuple(selected),
        logits=tuple(logits),
        convention=_CONVENTION,
    )


# --------------------------------------------------------------------------- #
# The minimum track record length
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MinTRLResult:
    """How many observations would make an observed Sharpe ratio significantly above a bar.

    Attributes:
        observations: MinTRL, ``1 + (1 - g3 SR + (g4 - 1)/4 SR^2) (z / (SR - SR*))^2``;
            a real number, since it is a length the formula solves for.
        sharpe: The per-period Sharpe ratio observed.
        skewness: The returns' skewness.
        kurtosis: Their kurtosis, not excess: 3 for a normal series.
        benchmark_sharpe: ``SR*``, per period.
        alpha: The significance level; ``z`` is the normal quantile at ``1 - alpha``.
        numerical_class: Host libm (class 3).
    """

    observations: float
    sharpe: float
    skewness: float
    kurtosis: float
    benchmark_sharpe: float
    alpha: float
    numerical_class: NumericalClass = NumericalClass.HOST_LIBM


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ResearchValidationError(f"{name} must be a finite number, got {value!r}.")
    return float(value)


def minimum_track_record_length(
    sharpe: float,
    skewness: float,
    kurtosis: float,
    benchmark_sharpe: float,
    alpha: float,
) -> MinTRLResult:
    """The minimum track record length (Bailey and López de Prado, 2012).

    The number of observations at which the probabilistic Sharpe ratio of a
    track record with this per-period Sharpe ratio, skewness and kurtosis
    reaches ``1 - alpha`` against ``benchmark_sharpe``: below it, the record is
    too short for the ratio to be significant at that level. The moments are
    the ones :func:`~alphalab.common.statistics.standardized_moments` reports,
    the kurtosis not excess.

    Raises:
        ResearchValidationError: If a figure is not finite, ``alpha`` is not in
            ``(0, 1)``, the Sharpe ratio is not above the benchmark (no length
            makes it significantly so), or the moments give the ratio's standard
            error no positive variance.
    """

    sharpe = _finite(sharpe, "sharpe")
    skewness = _finite(skewness, "skewness")
    kurtosis = _finite(kurtosis, "kurtosis")
    benchmark_sharpe = _finite(benchmark_sharpe, "benchmark_sharpe")
    alpha = _finite(alpha, "alpha")
    if not 0.0 < alpha < 1.0:
        raise ResearchValidationError(f"alpha must lie in (0, 1), got {alpha!r}.")
    if not sharpe > benchmark_sharpe:
        raise ResearchValidationError(
            f"A Sharpe ratio of {sharpe!r} is not above the benchmark {benchmark_sharpe!r}, so "
            "no track record is long enough to make it significantly so."
        )
    spread = 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe
    if not spread > 0.0:
        raise ResearchValidationError(
            f"Skewness {skewness!r} and kurtosis {kurtosis!r} give the Sharpe ratio's standard "
            "error no positive variance; they are not the moments of a distribution."
        )
    quantile = normal_quantile(1.0 - alpha)
    excess = sharpe - benchmark_sharpe
    return MinTRLResult(
        observations=1.0 + spread * (quantile / excess) * (quantile / excess),
        sharpe=sharpe,
        skewness=skewness,
        kurtosis=kurtosis,
        benchmark_sharpe=benchmark_sharpe,
        alpha=alpha,
    )


# --------------------------------------------------------------------------- #
# The probabilistic Sharpe ratio without independent returns
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AdjustedSharpeInference:
    """A probabilistic Sharpe ratio whose standard error allows for serial correlation.

    Attributes:
        inference: The ratio, its moments, the adjusted standard error and
            ``PSR(SR*)`` under it.
        lags: The autocorrelation lags allowed for.
        independent_spread: ``1 - g3 SR + (g4 - 1)/4 SR^2``: the variance factor
            :func:`~alphalab.research.sharpe_inference.probabilistic_sharpe_ratio`
            uses, which assumes independent returns.
        adjusted_spread: The same factor with the lags' Bartlett-weighted
            autocovariances added; the standard error is
            ``sqrt(adjusted_spread / (n - 1))``.
        numerical_class: Host libm (class 3).
    """

    inference: SharpeInference
    lags: int
    independent_spread: float
    adjusted_spread: float
    numerical_class: NumericalClass = NumericalClass.HOST_LIBM


def autocorrelation_adjusted_probabilistic_sharpe_ratio(
    returns: Sequence[float],
    benchmark_sharpe: float,
    *,
    lags: int,
) -> AdjustedSharpeInference:
    """PSR without assuming independent returns (Lo, 2002; Opdyke, 2007).

    The Sharpe ratio is a function of the mean and the variance. Its asymptotic
    variance is the delta method applied to their long-run covariance, which
    Newey and West's Bartlett-weighted autocovariances estimate up to ``lags``.
    In standardized terms, with ``a = (x - mean) / sd`` and ``b = a^2 - 1``, the
    variance factor is ``V_0 + 2 sum_k w_k q_k``, where

    * ``V_0 = 1 - g3 SR + (g4 - 1)/4 SR^2``, and
    * ``q_k = G_aa(k) - SR/2 (G_ab(k) + G_ba(k)) + SR^2/4 G_bb(k)``,

    with ``w_k = 1 - k / (lags + 1)`` and each ``G(k)`` the lag-``k`` cross moment
    averaged over ``n``. The first term is what the independent PSR uses
    (Opdyke's variance), so with ``lags=0`` this is
    :func:`~alphalab.research.sharpe_inference.probabilistic_sharpe_ratio`
    exactly. Positive autocorrelation, as in a smoothed or illiquid return
    series, widens the standard error and pulls the probability toward one
    half; negative autocorrelation narrows it.

    Args:
        returns: The (excess) returns, at one frequency, in time order; at
            least :data:`~alphalab.research.sharpe_inference.MINIMUM_OBSERVATIONS`.
        benchmark_sharpe: ``SR*``, per period.
        lags: The autocorrelation lags allowed for, from 0 to ``n - 2``.
            Required: how much dependence to allow is the caller's judgement.

    Raises:
        ResearchValidationError: As the independent PSR does, if ``lags`` is out
            of range, or if the adjusted variance factor is not positive, which
            a lag count too large for the series can produce.
    """

    series = tuple(returns)
    if len(series) < MINIMUM_OBSERVATIONS:
        raise ResearchValidationError(
            f"A Sharpe inference needs at least {MINIMUM_OBSERVATIONS} returns for their "
            f"skewness and kurtosis to mean anything; {len(series)} were given."
        )
    count = len(series)
    if isinstance(lags, bool) or not isinstance(lags, int) or not 0 <= lags <= count - 2:
        raise ResearchValidationError(
            f"lags must be an integer from 0 to {count - 2} for {count} returns, got {lags!r}."
        )
    benchmark_sharpe = _finite(benchmark_sharpe, "benchmark_sharpe")
    sharpe = per_period_sharpe(series)
    moments = standardized_moments(series)
    independent = 1.0 - moments.skewness * sharpe + (moments.kurtosis - 1.0) / 4.0 * sharpe * sharpe
    centre = mean(series)
    deviation = math.sqrt(sample_variance(series))
    a = [(value - centre) / deviation for value in series]
    b = [x * x - 1.0 for x in a]
    terms = []
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1)
        aa = math.fsum(a[t] * a[t - lag] for t in range(lag, count)) / count
        ab = math.fsum(a[t] * b[t - lag] for t in range(lag, count)) / count
        ba = math.fsum(b[t] * a[t - lag] for t in range(lag, count)) / count
        bb = math.fsum(b[t] * b[t - lag] for t in range(lag, count)) / count
        terms.append(2.0 * weight * (aa - sharpe / 2.0 * (ab + ba) + sharpe * sharpe / 4.0 * bb))
    adjusted = math.fsum([independent, *terms]) if terms else independent
    if not adjusted > 0.0:
        raise ResearchValidationError(
            f"With {lags} lags the Sharpe ratio's variance factor is {adjusted!r}, not "
            "positive: the autocovariances estimated over this series are too large for the "
            "lag count. Allow fewer lags."
        )
    standard_error = math.sqrt(adjusted / (count - 1))
    inference = SharpeInference(
        observations=count,
        sharpe=sharpe,
        skewness=moments.skewness,
        kurtosis=moments.kurtosis,
        benchmark_sharpe=benchmark_sharpe,
        standard_error=standard_error,
        probabilistic_sharpe=normal_cdf((sharpe - benchmark_sharpe) / standard_error),
    )
    return AdjustedSharpeInference(
        inference=inference,
        lags=lags,
        independent_spread=independent,
        adjusted_spread=adjusted,
    )
