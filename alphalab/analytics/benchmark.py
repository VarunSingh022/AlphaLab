"""Performance relative to a benchmark, with every undefined figure left undefined.

Until v3.11 AlphaLab reported a portfolio's performance only in isolation
(ledger FEA-002): beta existed as a risk-decomposition helper, and nothing
answered "did it beat what it was measured against, by how much, and at what
active risk?". :func:`benchmark_statistics` does, from two level series -- the
portfolio's equity curve and the benchmark's index levels.

Levels, not returns, are aligned
--------------------------------
Two return series with different holidays cannot be aligned by dropping the
unmatched rows: a portfolio return over Monday-to-Wednesday set beside a
benchmark return over Tuesday-to-Wednesday compares two different periods.
Levels can be: the statistics are computed from the returns between
consecutive instants *both* series have a level at, so every portfolio return
and its benchmark return span the same interval by construction. The instants
only one side has are counted and reported, never silently dropped.

What each figure is
-------------------
With ``p`` and ``b`` the aligned per-period returns, ``f`` the per-period
risk-free rate (the annual rate divided by the periods per year, the
convention :func:`~alphalab.analytics.metrics.sharpe_ratio` uses) and ``P``
the periods per year:

* **active return** -- ``mean(p - b) * P``, arithmetic and annualized.
* **tracking error** -- ``stdev(p - b) * sqrt(P)``, the sample (``n - 1``)
  deviation.
* **information ratio** -- active return over tracking error.
* **beta** -- the OLS slope of ``p`` on ``b``
  (:func:`~alphalab.common.statistics.linear_regression`, the repository's one
  regression; a constant shift of both sides by ``f`` leaves it unchanged).
* **alpha** -- Jensen's: ``(mean(p - f) - beta * mean(b - f)) * P``.
* **correlation** and **r_squared** -- Pearson's, and its square.
* **up capture** / **down capture** -- the portfolio's mean return over the
  periods the benchmark rose (fell), divided by the benchmark's mean over the
  same periods. Arithmetic means: the geometric variant some vendors publish is
  a different number and is not this one.
* **relative return** -- ``prod(1 + p) / prod(1 + b) - 1``, the geometric
  outperformance over the whole window, computed from the first and last
  shared levels (the products telescope) so no per-period rounding
  accumulates in it.

Undefined is ``None``: a tracking error over one period, an information ratio
over no active risk, a beta against a constant benchmark, a down capture over
a window in which the benchmark never fell. None of them is zero.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import (
    linear_regression,
    mean,
    pearson_correlation,
    sample_variance,
)

__all__ = ["BenchmarkBasis", "BenchmarkStatistics", "benchmark_statistics"]


@dataclass(frozen=True, slots=True)
class BenchmarkBasis:
    """How the relative figures are annualized, and against what riskless rate.

    Attributes:
        periods_per_year: Return periods in a year at the series' frequency --
            252 for a US equity daily curve, 365 for a crypto one. Required, as
            everywhere in :mod:`alphalab.analytics`.
        risk_free_rate: The annual riskless rate, simple, as a decimal fraction.
            Only Jensen's alpha reads it.
    """

    periods_per_year: float
    risk_free_rate: float

    def __post_init__(self) -> None:
        for name in ("periods_per_year", "risk_free_rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise AnalyticsValidationError(f"{name} must be a number, got {value!r}.")
            if not math.isfinite(value):
                raise AnalyticsValidationError(f"{name} must be finite, got {value!r}.")
        if self.periods_per_year <= 0:
            raise AnalyticsValidationError(
                f"periods_per_year must be positive, got {self.periods_per_year!r}."
            )


@dataclass(frozen=True, slots=True)
class BenchmarkStatistics:
    """A portfolio measured against a benchmark over their shared instants.

    Attributes:
        basis: The annualization and riskless rate used.
        observations: Aligned return periods the figures rest on.
        portfolio_only_instants: Instants with a portfolio level and no
            benchmark level. Excluded, and counted.
        benchmark_only_instants: The converse.
        active_return: Annualized mean active return, or ``None``.
        tracking_error: Annualized sample deviation of the active return, or
            ``None`` over fewer than two periods.
        information_ratio: ``active_return / tracking_error``, or ``None``.
        beta: The portfolio's OLS beta on the benchmark, or ``None``.
        alpha: Jensen's annualized alpha, or ``None`` with beta.
        correlation: Pearson correlation of the two return series, or ``None``.
        r_squared: ``correlation ** 2``, or ``None``.
        up_capture: Mean portfolio over mean benchmark return in the periods
            the benchmark rose, or ``None``.
        down_capture: The same over the periods it fell, or ``None``.
        relative_return: ``prod(1 + p) / prod(1 + b) - 1``. Always defined:
            every level is positive.
    """

    basis: BenchmarkBasis
    observations: int
    portfolio_only_instants: int
    benchmark_only_instants: int
    active_return: float | None
    tracking_error: float | None
    information_ratio: float | None
    beta: float | None
    alpha: float | None
    correlation: float | None
    r_squared: float | None
    up_capture: float | None
    down_capture: float | None
    relative_return: float


def _levels(series: Mapping[float, Decimal | float], what: str) -> dict[float, float]:
    levels: dict[float, float] = {}
    for stamp, level in series.items():
        if isinstance(level, bool) or not isinstance(level, int | float | Decimal):
            raise AnalyticsValidationError(f"The {what} level at {stamp!r} is {level!r}.")
        value = float(level)
        if not math.isfinite(value) or value <= 0.0:
            raise AnalyticsValidationError(
                f"The {what} level at {stamp!r} is {level!r}; a return is only defined "
                "between positive, finite levels."
            )
        levels[float(stamp)] = value
    return levels


def _capture(portfolio: list[float], benchmark: list[float], rising: bool) -> float | None:
    chosen = [
        (p, b)
        for p, b in zip(portfolio, benchmark, strict=True)
        if (b > 0.0 if rising else b < 0.0)
    ]
    if not chosen:
        return None
    benchmark_mean = mean([b for _, b in chosen])
    return mean([p for p, _ in chosen]) / benchmark_mean


def benchmark_statistics(
    portfolio: Mapping[float, Decimal | float],
    benchmark: Mapping[float, Decimal | float],
    basis: BenchmarkBasis,
) -> BenchmarkStatistics:
    """Measure a portfolio's equity curve against a benchmark's levels.

    Args:
        portfolio: Instant to the portfolio's value (an equity curve).
        benchmark: Instant to the benchmark's level.
        basis: Annualization and riskless rate.

    Raises:
        AnalyticsValidationError: If a level is not a positive finite number,
            or the two series share fewer than two instants -- one shared
            instant yields no return at all.
    """

    mine = _levels(portfolio, "portfolio")
    theirs = _levels(benchmark, "benchmark")
    shared = sorted(set(mine) & set(theirs))
    if len(shared) < 2:
        raise AnalyticsValidationError(
            f"The portfolio and the benchmark share {len(shared)} instant(s); a relative "
            "return needs at least two, so that both sides span the same period."
        )

    p = [mine[b] / mine[a] - 1.0 for a, b in pairwise(shared)]
    q = [theirs[b] / theirs[a] - 1.0 for a, b in pairwise(shared)]
    periods = basis.periods_per_year
    active = [x - y for x, y in zip(p, q, strict=True)]

    tracking_error: float | None = None
    information_ratio: float | None = None
    if len(active) >= 2:
        tracking_error = math.sqrt(sample_variance(active)) * math.sqrt(periods)
        if tracking_error > 0.0:
            information_ratio = mean(active) * periods / tracking_error

    beta: float | None = None
    alpha: float | None = None
    r_squared: float | None = None
    correlation: float | None = None
    try:
        fit = linear_regression(p, q)
    except AlphaLabValidationError:
        fit = None  # fewer than two periods, or a constant benchmark
    if fit is not None:
        beta = fit.slope
        per_period_rate = basis.risk_free_rate / periods
        alpha = (mean(p) - per_period_rate - beta * (mean(q) - per_period_rate)) * periods
        try:
            correlation = pearson_correlation(p, q)
        except AlphaLabValidationError:
            correlation = None  # a constant portfolio: no correlation to report
        r_squared = None if correlation is None else correlation * correlation

    first, last = shared[0], shared[-1]
    relative = (mine[last] / mine[first]) / (theirs[last] / theirs[first]) - 1.0

    return BenchmarkStatistics(
        basis=basis,
        observations=len(p),
        portfolio_only_instants=len(set(mine) - set(theirs)),
        benchmark_only_instants=len(set(theirs) - set(mine)),
        active_return=mean(active) * periods,
        tracking_error=tracking_error,
        information_ratio=information_ratio,
        beta=beta,
        alpha=alpha,
        correlation=correlation,
        r_squared=r_squared,
        up_capture=_capture(p, q, rising=True),
        down_capture=_capture(p, q, rising=False),
        relative_return=relative,
    )
