"""Deterministic statistics, and the tie and sample rules they follow.

Every number v3.2's research layer reports -- an information coefficient, a
z-score, a quantile bucket, a neutralization residual -- rests on one of the
functions here. They live in :mod:`alphalab.common` for the reason
:mod:`alphalab.common.point_in_time` does: the arithmetic has no domain in it,
and two packages need it. ``alphalab.factor_library`` computes cross-sectional
statistics over a panel, ``alphalab.research`` computes them over a signal and
its forward returns, and a correlation that meant two slightly different things
in those two places would make a diagnostic incomparable with the factor it was
measured on.

This module was also the occasion to stop spelling sample variance four times.
Before v3.2 :func:`~alphalab.analytics.returns.annualized_volatility`,
:func:`~alphalab.analytics.rolling.rolling_volatility` and
:func:`~alphalab.research.metrics.calculate_volatility` each carried their own
copy of ``sum((x - mean) ** 2) / (n - 1)``. They now call :func:`sample_variance`,
which is the same expression in the same order and therefore the same float --
the three functions' published numbers are unchanged, and there is one place
left where the estimator could be got wrong.

An undefined statistic raises
-----------------------------

A correlation over one observation, a variance over one observation and a
z-score over a constant series are not zero; they are undefined. Every function
here refuses rather than returning a placeholder, because a placeholder is
indistinguishable from a real measurement once it has been rounded and put in a
table. Callers that must *report* insufficiency rather than fail -- which is
most of the research layer -- check the sample first and say so in their own
result. That is the division :mod:`alphalab.research.signals` and
:mod:`alphalab.factor_library.ic` both follow: the mathematics refuses, the
diagnostic explains.

Ties are a decision, not a detail
---------------------------------

Ranking is where a "deterministic" implementation usually is not. Two equal
values have no natural order, and a library that leaves the outcome to the
sort's stability makes a factor's ranks depend on the order its assets happened
to arrive in. :class:`RankMethod` names the four standard conventions and
:func:`ranks` implements each exactly, so a caller states which one they meant
and gets the same answer in any input order -- ``AVERAGE`` and ``ORDINAL`` are
both deterministic, and they are deterministically *different*.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

from alphalab.common.exceptions import AlphaLabValidationError

__all__ = [
    "LinearFit",
    "RankMethod",
    "TieBreak",
    "bucket_index",
    "linear_regression",
    "mean",
    "median",
    "pearson_correlation",
    "percentile",
    "rank_correlation",
    "ranks",
    "sample_covariance",
    "sample_variance",
    "standard_deviation",
    "standardize",
    "winsorize",
]


class RankMethod(Enum):
    """How :func:`ranks` resolves a tie.

    ``AVERAGE`` is the convention Spearman's rank correlation is defined
    against and the default anywhere this repository ranks for a statistic.
    ``MIN`` ("competition ranking") and ``MAX`` are the two standard
    alternatives. ``ORDINAL`` breaks ties by the value's position in the input,
    which is deterministic but *order-dependent* by construction -- it is
    offered because a quantile bucketing sometimes needs every rank distinct,
    and it is never used for a correlation.
    """

    AVERAGE = auto()
    MIN = auto()
    MAX = auto()
    ORDINAL = auto()


class TieBreak(Enum):
    """Which end of a tie a bucketing sends the tied values to.

    Quantile bucketing over a series with repeated values cannot give every
    bucket an equal count, and which bucket a run of equal values lands in is a
    choice rather than a fact. Stating it is what makes two runs of the same
    study agree.
    """

    #: Tied values all take the lowest bucket their run spans.
    LOW = auto()
    #: Tied values all take the highest bucket their run spans.
    HIGH = auto()
    #: Tied values are split across the buckets their run spans, in input order.
    SPREAD = auto()


@dataclass(frozen=True, slots=True)
class LinearFit:
    """One ordinary least squares fit of ``y`` on a single ``x``.

    Attributes:
        slope: The fitted coefficient on ``x``.
        intercept: The fitted constant.
        r_squared: Share of ``y``'s variance the fit explains, in ``[0, 1]``.
        observations: How many pairs the fit was computed from.
        residuals: ``y - (intercept + slope * x)``, in input order. What
            beta neutralization keeps.
    """

    slope: float
    intercept: float
    r_squared: float
    observations: int
    residuals: tuple[float, ...]


def _require_pairs(xs: Sequence[float], ys: Sequence[float], what: str) -> int:
    if len(xs) != len(ys):
        raise AlphaLabValidationError(
            f"{what} needs one x for every y, got {len(xs)} and {len(ys)}. A pairing "
            "that has to be guessed is not a pairing."
        )
    if len(xs) < 2:
        raise AlphaLabValidationError(
            f"{what} is undefined over {len(xs)} observation(s); at least 2 are needed."
        )
    return len(xs)


def mean(values: Sequence[float]) -> float:
    """The arithmetic mean.

    Raises:
        AlphaLabValidationError: If ``values`` is empty. The mean of nothing is
            not zero.
    """

    if not values:
        raise AlphaLabValidationError("The mean of an empty sequence is undefined.")
    return sum(values) / len(values)


def sample_variance(values: Sequence[float]) -> float:
    """The unbiased (``n - 1``) sample variance.

    The estimator this repository uses everywhere. The population form is not
    offered: mixing the two across a codebase produces numbers that differ by a
    factor of ``n / (n - 1)`` and agree closely enough to look like rounding.

    Raises:
        AlphaLabValidationError: If fewer than two values are given.
    """

    if len(values) < 2:
        raise AlphaLabValidationError(
            f"Sample variance is undefined over {len(values)} observation(s); "
            "at least 2 are needed."
        )
    average = sum(values) / len(values)
    return sum((value - average) ** 2 for value in values) / (len(values) - 1)


def sample_covariance(xs: Sequence[float], ys: Sequence[float]) -> float:
    """The unbiased (``n - 1``) sample covariance of two equal-length series.

    The same estimator as :func:`sample_variance`, and deliberately built from
    the same expression in the same order, so that ``sample_covariance(x, x)``
    is exactly ``sample_variance(x)`` -- float-for-float, not merely close. A
    covariance matrix whose diagonal disagreed with the variances the rest of
    the repository reports would give a portfolio volatility that no position's
    own volatility could be reconciled against.

    Raises:
        AlphaLabValidationError: If the lengths differ or fewer than two pairs
            are given. A covariance over one observation is undefined, not zero.
    """

    count = _require_pairs(xs, ys, "A sample covariance")
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / (count - 1)


def standard_deviation(values: Sequence[float]) -> float:
    """The square root of :func:`sample_variance`.

    Raises:
        AlphaLabValidationError: If fewer than two values are given.
    """

    return math.sqrt(sample_variance(values))


def median(values: Sequence[float]) -> float:
    """The middle value, averaging the two middles for an even count.

    Raises:
        AlphaLabValidationError: If ``values`` is empty.
    """

    if not values:
        raise AlphaLabValidationError("The median of an empty sequence is undefined.")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def percentile(values: Sequence[float], fraction: float) -> float:
    """The linearly interpolated percentile at ``fraction`` in ``[0, 1]``.

    Interpolation is stated rather than assumed: ``fraction`` selects the
    position ``(n - 1) * fraction`` in the sorted values and the two
    neighbouring order statistics are blended. This is the ``numpy`` "linear"
    convention and the one a reader is most likely to expect, and naming it
    here is what stops a later change from silently moving every published
    quantile.

    Raises:
        AlphaLabValidationError: If ``values`` is empty or ``fraction`` is
            outside ``[0, 1]``.
    """

    if not values:
        raise AlphaLabValidationError("A percentile of an empty sequence is undefined.")
    if not 0.0 <= fraction <= 1.0:
        raise AlphaLabValidationError(f"fraction must lie in [0, 1], got {fraction!r}.")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def ranks(values: Sequence[float], method: RankMethod = RankMethod.AVERAGE) -> tuple[float, ...]:
    """Rank ``values`` from 1 (smallest) upward, resolving ties by ``method``.

    Returned in *input* order, so a rank can be read against the asset it
    belongs to without a second lookup.

    Raises:
        AlphaLabValidationError: If ``values`` is empty.
    """

    if not values:
        raise AlphaLabValidationError("Ranking an empty sequence is undefined.")

    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    result = [0.0] * len(values)

    if method is RankMethod.ORDINAL:
        for position, index in enumerate(order):
            result[index] = float(position + 1)
        return tuple(result)

    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[stop]] == values[order[start]]:
            stop += 1
        if method is RankMethod.AVERAGE:
            assigned = (start + stop + 1) / 2.0
        elif method is RankMethod.MIN:
            assigned = float(start + 1)
        else:
            assigned = float(stop)
        for index in order[start:stop]:
            result[index] = assigned
        start = stop

    return tuple(result)


def pearson_correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    """The Pearson product-moment correlation of two equal-length series.

    Raises:
        AlphaLabValidationError: If the lengths differ, if fewer than two pairs
            are given, or if either series is constant -- a correlation with a
            constant is undefined, not zero, and reporting zero would read as
            "measured, and unrelated".
    """

    count = _require_pairs(xs, ys, "A Pearson correlation")
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count

    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    variance_y = sum((y - mean_y) ** 2 for y in ys)

    if variance_x == 0.0 or variance_y == 0.0:
        constant = "x" if variance_x == 0.0 else "y"
        raise AlphaLabValidationError(
            f"A Pearson correlation is undefined because {constant} is constant over all "
            f"{count} observations. Zero would read as a measured absence of relationship."
        )

    return covariance / math.sqrt(variance_x * variance_y)


def rank_correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Spearman's rank correlation: :func:`pearson_correlation` of the ranks.

    Ties take :attr:`RankMethod.AVERAGE`, which is what makes this Spearman's
    coefficient rather than an approximation of it.

    Raises:
        AlphaLabValidationError: If the lengths differ, if fewer than two pairs
            are given, or if either series is constant *in rank* -- which
            happens exactly when all of its values are equal.
    """

    _require_pairs(xs, ys, "A rank correlation")
    return pearson_correlation(ranks(xs), ranks(ys))


def linear_regression(ys: Sequence[float], xs: Sequence[float]) -> LinearFit:
    """Fit ``y = intercept + slope * x`` by ordinary least squares.

    One regressor and an intercept, which is what beta neutralization and a
    market-exposure residualization need. Multiple continuous regressors are
    deliberately not offered here; see
    :mod:`alphalab.factor_library.neutralization` for which neutralizations
    AlphaLab implements and why the list stops where it does.

    Raises:
        AlphaLabValidationError: If the lengths differ, if fewer than two pairs
            are given, or if ``xs`` is constant, which leaves the slope
            undetermined.
    """

    count = _require_pairs(xs, ys, "A linear regression")
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count

    variance_x = sum((x - mean_x) ** 2 for x in xs)
    if variance_x == 0.0:
        raise AlphaLabValidationError(
            f"A linear regression is undetermined because x is constant over all {count} "
            "observations; every slope fits equally well."
        )

    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    slope = covariance / variance_x
    intercept = mean_y - slope * mean_x
    residuals = tuple(y - (intercept + slope * x) for x, y in zip(xs, ys, strict=True))

    total = sum((y - mean_y) ** 2 for y in ys)
    explained = 0.0 if total == 0.0 else 1.0 - sum(r**2 for r in residuals) / total

    return LinearFit(
        slope=slope,
        intercept=intercept,
        r_squared=explained,
        observations=count,
        residuals=residuals,
    )


def standardize(values: Sequence[float]) -> tuple[float, ...]:
    """Z-score ``values`` against their own mean and sample standard deviation.

    Raises:
        AlphaLabValidationError: If fewer than two values are given, or if they
            are all equal -- a constant series has no scale to standardize
            against, and returning zeros would claim every observation sat
            exactly at a mean that means nothing.
    """

    deviation = standard_deviation(values)
    if deviation == 0.0:
        raise AlphaLabValidationError(
            f"Cannot standardize {len(values)} identical values: the series has no "
            "dispersion, so a z-score has no scale."
        )
    average = sum(values) / len(values)
    return tuple((value - average) / deviation for value in values)


def winsorize(values: Sequence[float], lower: float, upper: float) -> tuple[float, ...]:
    """Clip ``values`` to the ``lower`` and ``upper`` percentiles of themselves.

    Clipping, never dropping: the returned tuple is the same length and in the
    same order, so a winsorized factor still lines up with its assets.

    Raises:
        AlphaLabValidationError: If ``values`` is empty, if either fraction is
            outside ``[0, 1]``, or if ``lower`` exceeds ``upper``.
    """

    if lower > upper:
        raise AlphaLabValidationError(
            f"winsorize lower={lower!r} is above upper={upper!r}; the bounds are crossed."
        )
    low = percentile(values, lower)
    high = percentile(values, upper)
    return tuple(min(max(value, low), high) for value in values)


def bucket_index(
    values: Sequence[float], buckets: int, tie_break: TieBreak = TieBreak.LOW
) -> tuple[int, ...]:
    """Assign each value to one of ``buckets`` quantile buckets, 0 being lowest.

    Returned in input order. Bucketing is done on :func:`ranks` rather than on
    the values, so it is invariant to any monotone transform of the factor --
    which is the property a quantile study is asking for.

    ``tie_break`` decides where a run of equal values goes; see
    :class:`TieBreak`. With :attr:`TieBreak.SPREAD` the tied run is split in
    input order, so the bucket counts stay even and the assignment still
    reproduces exactly.

    Raises:
        AlphaLabValidationError: If ``values`` is empty, if ``buckets`` is below
            1, or if there are fewer values than buckets -- a bucketing that
            leaves buckets empty reports quantiles nothing was measured in.
    """

    if buckets < 1:
        raise AlphaLabValidationError(f"buckets must be at least 1, got {buckets}.")
    if not values:
        raise AlphaLabValidationError("Bucketing an empty sequence is undefined.")
    if len(values) < buckets:
        raise AlphaLabValidationError(
            f"Cannot sort {len(values)} value(s) into {buckets} buckets: at least "
            f"{buckets} are needed for every bucket to hold something."
        )

    count = len(values)

    if tie_break is TieBreak.SPREAD:
        ordinal = ranks(values, RankMethod.ORDINAL)
        return tuple(min(buckets - 1, int((rank - 1) * buckets // count)) for rank in ordinal)

    method = RankMethod.MIN if tie_break is TieBreak.LOW else RankMethod.MAX
    tied = ranks(values, method)
    return tuple(min(buckets - 1, int((rank - 1) * buckets // count)) for rank in tied)
