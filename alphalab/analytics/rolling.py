"""Rolling window analytics."""

import math

from alphalab.analytics.metrics import sharpe_ratio
from alphalab.analytics.returns import geometric_return
from alphalab.common.statistics import sample_variance


def rolling_return(returns: tuple[float, ...], window: int) -> tuple[float, ...]:
    """Calculates the geometric return over a rolling window."""
    if len(returns) < window or window <= 0:
        return ()

    result = []
    for i in range(len(returns) - window + 1):
        window_slice = returns[i : i + window]
        result.append(geometric_return(window_slice))
    return tuple(result)


def rolling_volatility(
    returns: tuple[float, ...], window: int, periods: int = 252
) -> tuple[float, ...]:
    """Calculates annualized volatility over a rolling window."""
    if len(returns) < window or window < 2:
        return ()

    result = []
    for i in range(len(returns) - window + 1):
        window_slice = returns[i : i + window]
        result.append(math.sqrt(sample_variance(window_slice)) * math.sqrt(periods))

    return tuple(result)


def rolling_sharpe(
    returns: tuple[float, ...], window: int, risk_free_rate: float = 0.0, periods: int = 252
) -> tuple[float, ...]:
    """Calculates Sharpe Ratio over a rolling window."""
    if len(returns) < window or window < 2:
        return ()

    result = []
    for i in range(len(returns) - window + 1):
        window_slice = returns[i : i + window]
        result.append(sharpe_ratio(window_slice, risk_free_rate, periods))
    return tuple(result)
