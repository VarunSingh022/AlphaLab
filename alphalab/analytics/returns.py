"""Return metrics and calculations."""

import math
from decimal import Decimal


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
    """Calculates annualized standard deviation of returns."""
    if len(returns) < 2:
        return 0.0

    mean_ret = arithmetic_return(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)

    return math.sqrt(variance) * math.sqrt(periods)
