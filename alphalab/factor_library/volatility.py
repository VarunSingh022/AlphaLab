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
    periods_per_year: int,
) -> FactorResult:
    """Computes annualized volatility of simple returns over the lookback window.

    ``periods_per_year`` is how many bars make a year -- 252 for trading days,
    about 98,280 for the minutes of a US equity session. Required since v3.10:
    it defaulted to 252 whatever the bars were (ledger API-003).

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
    # At least two returns, by the lookback check above, so it is defined.
    assert vol is not None

    return FactorResult(
        feature_id=feature_id,
        version=version,
        asset_id=prices.asset_id,
        value=vol,
        timestamp=timestamp,
    )
