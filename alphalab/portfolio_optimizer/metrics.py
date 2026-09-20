"""Immutable metrics tracking and deterministic mathematical evaluators."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from alphalab.common.statistics import sample_variance


@dataclass(frozen=True, slots=True)
class PortfolioMetrics:
    portfolio_id: str
    timestamp: float
    total_return: float
    annual_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown: float
    turnover: float
    diversification_ratio: float


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


def calculate_volatility(returns: Sequence[float], periods: int = 252) -> float:
    """Annualized volatility, over the one shared unbiased estimator.

    ``sqrt(var * periods)`` rather than ``sqrt(var) * sqrt(periods)``: the two
    are equal in exact arithmetic and not always in floating point, and this
    module has always used the first. Changing it would move published numbers
    for no reason.
    """
    if len(returns) < 2:
        return 0.0
    return math.sqrt(sample_variance(returns) * periods)
