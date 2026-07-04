"""Deterministic mathematical operations for research metrics."""

import math
from collections.abc import Sequence


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
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)


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
