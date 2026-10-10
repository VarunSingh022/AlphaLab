"""Advanced risk-adjusted return ratios and VaR calculations.

``sharpe_ratio`` takes its dispersion from
:func:`alphalab.common.statistics.sample_variance`, the one unbiased estimator
in the repository. ``sortino_ratio`` deliberately does not: downside
semideviation divides the sum of squared *negative* excess returns by the count
of **all** returns, which is a different estimator rather than the same one
applied to a subset, and routing it through the shared function would quietly
change every Sortino ratio AlphaLab has ever reported.

Undefined is ``None``
---------------------
A Sharpe ratio of a constant series, a Sortino ratio with no downside, a Calmar
ratio with no drawdown and a VaR of no returns are not zero: they are undefined,
and until v3.10 every one of them was reported as ``0.0`` -- a number
indistinguishable from a measured zero once it reached a table. They are now
``None``. ``nowandfuture.md`` invariant 21 has always said so; the code now
agrees with it.

Annualization is stated
-----------------------
``periods`` -- how many return periods make a year -- is required. It defaulted
to 252, the US equity trading-day count, which is wrong for a crypto book (365),
an intraday one, a weekly one, and any market with another holiday calendar. The
analytics engine derives it from the equity curve or takes a declared figure,
and records which in the report.
"""

import math

from alphalab.common.statistics import sample_variance


def _require_periods(periods: float) -> None:
    if not math.isfinite(periods) or periods <= 0:
        raise ValueError(f"periods per year must be a positive finite number, got {periods!r}.")


def sharpe_ratio(returns: tuple[float, ...], risk_free_rate: float, periods: float) -> float | None:
    """The annualized Sharpe ratio, or ``None`` when it is undefined.

    Undefined over fewer than two returns and over returns with no dispersion.
    """

    _require_periods(periods)
    if len(returns) < 2:
        return None

    excess_returns = tuple(r - (risk_free_rate / periods) for r in returns)
    mean_excess = sum(excess_returns) / len(excess_returns)

    stdev = math.sqrt(sample_variance(excess_returns))

    if stdev == 0.0:
        return None

    return (mean_excess / stdev) * math.sqrt(periods)


def sortino_ratio(
    returns: tuple[float, ...], risk_free_rate: float, periods: float
) -> float | None:
    """The annualized Sortino ratio, or ``None`` when it is undefined.

    Undefined over fewer than two returns and when no excess return is
    negative -- the downside deviation is zero, and the ratio is not a number.
    """

    _require_periods(periods)
    if len(returns) < 2:
        return None

    period_rf = risk_free_rate / periods
    excess_returns = tuple(r - period_rf for r in returns)
    mean_excess = sum(excess_returns) / len(excess_returns)

    downside_sq = [r**2 for r in excess_returns if r < 0.0]
    if not downside_sq:
        return None

    downside_dev = math.sqrt(sum(downside_sq) / len(returns))
    return (mean_excess / downside_dev) * math.sqrt(periods)


def calmar_ratio(cagr_value: float | None, max_drawdown: float) -> float | None:
    """CAGR over maximum drawdown, or ``None`` when either is missing or zero."""

    if cagr_value is None or max_drawdown <= 0.0:
        return None
    return cagr_value / max_drawdown


def _historical_quantile(sorted_returns: list[float], confidence: float) -> float:
    k = (len(sorted_returns) - 1) * (1.0 - confidence)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_returns[int(k)]
    # Linear interpolation between the two neighbouring order statistics.
    return sorted_returns[int(f)] * (c - k) + sorted_returns[int(c)] * (k - f)


def value_at_risk(returns: tuple[float, ...], confidence: float = 0.95) -> float | None:
    """Historical Value at Risk: the return at the ``1 - confidence`` quantile.

    Unrounded: until v3.10 the result was rounded to six decimals inside the
    computation, which a caller could not undo. ``None`` for no returns.
    """

    if not returns:
        return None
    return _historical_quantile(sorted(returns), confidence)


def conditional_var(returns: tuple[float, ...], confidence: float = 0.95) -> float | None:
    """Conditional VaR (expected shortfall): the mean return at or below VaR.

    The tail is selected against the **unrounded** threshold. Until v3.10 it
    used a threshold rounded to six decimals, so a return a hair below the true
    VaR could be left out of its own tail. ``None`` for no returns.
    """

    if not returns:
        return None

    ordered = sorted(returns)
    threshold = _historical_quantile(ordered, confidence)
    tail = [r for r in ordered if r <= threshold]
    # The lowest return is always at or below any interpolated quantile, so
    # the tail is never empty.
    return sum(tail) / len(tail)
