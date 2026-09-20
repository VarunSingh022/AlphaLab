"""Return metrics and calculations."""

import math
from decimal import Decimal

from alphalab.common.statistics import sample_variance


def total_return(start_value: Decimal, end_value: Decimal) -> float:
    """Calculates the absolute total return between two capital marks."""
    if start_value <= Decimal("0"):
        return 0.0
    return float((end_value - start_value) / start_value)


def cagr(start_value: Decimal, end_value: Decimal, years: float) -> float:
    """Calculates Compound Annual Growth Rate."""
    if start_value <= Decimal("0") or end_value <= Decimal("0") or years <= 0.0:
        return 0.0
    return float(math.pow(float(end_value / start_value), 1.0 / years)) - 1.0


def arithmetic_return(returns: tuple[float, ...]) -> float:
    """Calculates the simple average return."""
    if not returns:
        return 0.0
    return sum(returns) / len(returns)


def geometric_return(returns: tuple[float, ...]) -> float:
    """Calculates the geometric average return."""
    if not returns:
        return 0.0

    product = 1.0
    for r in returns:
        product *= 1.0 + r

    return math.pow(product, 1.0 / len(returns)) - 1.0


def annualized_volatility(returns: tuple[float, ...], periods: int = 252) -> float:
    """Calculates annualized standard deviation of returns.

    The dispersion is `alphalab.common.statistics.sample_variance`, which is the
    one unbiased estimator in the repository rather than a fourth copy of it.
    Fewer than two returns has no variance; this reports 0.0 rather than raising
    because a report over an empty window is a normal state for it to be in.
    """
    if len(returns) < 2:
        return 0.0

    return math.sqrt(sample_variance(returns)) * math.sqrt(periods)
