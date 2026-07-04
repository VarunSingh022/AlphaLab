"""Immutable metrics tracking and deterministic mathematical evaluators."""

import math
from collections.abc import Sequence
from dataclasses import dataclass


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
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var * periods)
