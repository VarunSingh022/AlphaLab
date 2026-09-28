"""Deterministic mathematical operations for research metrics.

These are the v1 research engine's metrics, and they assume a **daily** return
series: annualization defaults to 252 periods, and :func:`calculate_sharpe`
annualizes with 252 whatever it is given. The canonical run's analytics do not
(:class:`~alphalab.analytics.report.PerformanceReport` records the periodicity
it used, observed or declared). The v1 engine is consolidated into the one
research authority in v3.12 (ledger RES-001, SCF-003); until then, pass the
periods a year holds for anything but daily returns.
"""

import math
from collections.abc import Sequence

from alphalab.common.statistics import sample_variance


def calculate_cagr(returns: Sequence[float], periods_per_year: int = 252) -> float:
    if not returns:
        return 0.0
    cumulative = 1.0
    for r in returns:
        cumulative *= 1.0 + r
    if cumulative <= 0:
        return -1.0
    years = len(returns) / periods_per_year
    return (cumulative ** (1.0 / years)) - 1.0 if years > 0 else 0.0


def calculate_volatility(returns: Sequence[float], periods_per_year: int = 252) -> float:
    """Annualized standard deviation, over the one shared unbiased estimator."""
    if len(returns) < 2:
        return 0.0
    return math.sqrt(sample_variance(returns)) * math.sqrt(periods_per_year)


def calculate_sharpe(returns: Sequence[float], risk_free_rate: float = 0.0) -> float:
    vol = calculate_volatility(returns)
    if vol == 0.0:
        return 0.0
    mean_return = (sum(returns) / len(returns)) * 252
    return (mean_return - risk_free_rate) / vol


def calculate_max_drawdown(returns: Sequence[float]) -> float:
    max_dd = 0.0
    peak = 1.0
    current = 1.0
    for r in returns:
        current *= 1.0 + r
        if current > peak:
            peak = current
        dd = (peak - current) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd
