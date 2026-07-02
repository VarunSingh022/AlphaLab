"""Advanced risk-adjusted return ratios and VaR calculations."""

import math


def sharpe_ratio(
    returns: tuple[float, ...], risk_free_rate: float = 0.0, periods: int = 252
) -> float:
    """Calculates the annualized Sharpe Ratio."""
    if len(returns) < 2:
        return 0.0
    
    excess_returns = tuple(r - (risk_free_rate / periods) for r in returns)
    mean_excess = sum(excess_returns) / len(excess_returns)
    
    variance = sum((r - mean_excess) ** 2 for r in excess_returns) / (len(excess_returns) - 1)
    stdev = math.sqrt(variance)
    
    if stdev == 0.0:
        return 0.0
        
    return (mean_excess / stdev) * math.sqrt(periods)


def sortino_ratio(
    returns: tuple[float, ...], risk_free_rate: float = 0.0, periods: int = 252
) -> float:
    """Calculates the annualized Sortino Ratio focusing on downside volatility."""
    if len(returns) < 2:
        return 0.0
        
    period_rf = risk_free_rate / periods
    excess_returns = tuple(r - period_rf for r in returns)
    mean_excess = sum(excess_returns) / len(excess_returns)
    
    downside_sq = [r ** 2 for r in excess_returns if r < 0.0]
    if not downside_sq:
        return 0.0  # Infinite/undefined when there's no downside
        
    downside_dev = math.sqrt(sum(downside_sq) / len(returns))
    result = (mean_excess / downside_dev) * math.sqrt(periods)
    
    if abs(result) < 1e-12:
        return 0.0
        
    return result


def calmar_ratio(cagr_value: float, max_drawdown: float) -> float:
    """Calculates the Calmar Ratio."""
    if max_drawdown <= 0.0:
        return 0.0
    return cagr_value / max_drawdown


def value_at_risk(returns: tuple[float, ...], confidence: float = 0.95) -> float:
    """Calculates historical Value at Risk (VaR)."""
    if not returns:
        return 0.0
    
    sorted_ret = sorted(returns)
    k = (len(sorted_ret) - 1) * (1.0 - confidence)
    f = math.floor(k)
    c = math.ceil(k)
    
    if f == c:
        return round(sorted_ret[int(k)], 6)
        
    # Linear interpolation
    d0 = sorted_ret[int(f)]
    d1 = sorted_ret[int(c)]
    result = d0 * (c - k) + d1 * (k - f)
    
    return round(result, 6)


def conditional_var(returns: tuple[float, ...], confidence: float = 0.95) -> float:
    """Calculates Conditional VaR (Expected Shortfall)."""
    if not returns:
        return 0.0
        
    var_threshold = value_at_risk(returns, confidence)
    tail = [r for r in returns if r <= var_threshold]
    
    if not tail:
        return 0.0
        
    result = sum(tail) / len(tail)
    return round(result, 6)
