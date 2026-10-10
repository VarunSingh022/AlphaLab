"""Rolling window analytics."""

import math

from alphalab.analytics.metrics import sharpe_ratio
from alphalab.analytics.returns import geometric_return
from alphalab.common.statistics import sample_variance


def rolling_return(returns: tuple[float, ...], window: int) -> tuple[float, ...]:
    """Calculates the geometric return over a rolling window."""
    if len(returns) < window or window <= 0:
        return ()

    result: list[float] = []
    for i in range(len(returns) - window + 1):
        window_slice = returns[i : i + window]
        value = geometric_return(window_slice)
        if value is None:
            raise ValueError(
                f"The window starting at {i} holds a return below -100% and has no geometric mean."
            )
        result.append(value)
    return tuple(result)


def rolling_volatility(
    returns: tuple[float, ...], window: int, periods: float
) -> tuple[float, ...]:
    """Calculates annualized volatility over a rolling window.

    ``periods`` -- return periods per year -- is required; it defaulted to 252.
    """
    if len(returns) < window or window < 2:
        return ()

    result = []
    for i in range(len(returns) - window + 1):
        window_slice = returns[i : i + window]
        result.append(math.sqrt(sample_variance(window_slice)) * math.sqrt(periods))

    return tuple(result)


def rolling_sharpe(
    returns: tuple[float, ...], window: int, risk_free_rate: float, periods: float
) -> tuple[float | None, ...]:
    """Calculates Sharpe Ratio over a rolling window.

    A window with no dispersion has no Sharpe ratio and contributes ``None``.
    ``risk_free_rate`` and ``periods`` are required.
    """
    if len(returns) < window or window < 2:
        return ()

    result: list[float | None] = []
    for i in range(len(returns) - window + 1):
        window_slice = returns[i : i + window]
        result.append(sharpe_ratio(window_slice, risk_free_rate, periods))
    return tuple(result)
