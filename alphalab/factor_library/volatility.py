"""Volatility factor: annualized standard deviation of returns over a lookback window."""

from alphalab.analytics.returns import annualized_volatility
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.inputs import PriceSeries
from alphalab.factor_library.result import FactorResult


def compute_volatility(
    prices: PriceSeries,
    feature_id: str,
    version: int,
    lookback_periods: int,
    timestamp: float,
    periods_per_year: int = 252,
) -> FactorResult:
    """Computes annualized volatility of simple returns over the lookback window.

    Reuses `alphalab.analytics.returns.annualized_volatility` on returns derived from
    consecutive bar closes, rather than recomputing variance independently.

    Raises:
        FactorInputError: If fewer than `lookback_periods + 1` bars are available,
            or lookback_periods is below the minimum of 2 required to compute a
            standard deviation.
    """
    if lookback_periods < 2:
        raise FactorInputError(f"lookback_periods must be >= 2, got {lookback_periods}.")

    required = lookback_periods + 1
    if len(prices.bars) < required:
        raise FactorInputError(
            f"Volatility over {lookback_periods} periods requires {required} bars, "
            f"got {len(prices.bars)}."
        )

    window = prices.bars[-required:]
    returns = tuple(
        float((window[i].close - window[i - 1].close) / window[i - 1].close)
        for i in range(1, len(window))
    )
    vol = annualized_volatility(returns, periods_per_year)

    return FactorResult(
        feature_id=feature_id,
        version=version,
        asset_id=prices.asset_id,
        value=vol,
        timestamp=timestamp,
    )
