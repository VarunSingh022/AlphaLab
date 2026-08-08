"""Momentum factor: trailing price return over a lookback window."""

from alphalab.analytics.returns import total_return
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.inputs import PriceSeries
from alphalab.factor_library.result import FactorResult


def compute_momentum(
    prices: PriceSeries, feature_id: str, version: int, lookback_periods: int, timestamp: float
) -> FactorResult:
    """Computes trailing total return over the last `lookback_periods` bars.

    Momentum is the total return from the close `lookback_periods` bars ago to the
    most recent close, reusing `alphalab.analytics.returns.total_return` rather than
    recomputing return math independently.

    Raises:
        FactorInputError: If fewer than `lookback_periods + 1` bars are available,
            or lookback_periods is not positive.
    """
    if lookback_periods <= 0:
        raise FactorInputError(f"lookback_periods must be positive, got {lookback_periods}.")

    required = lookback_periods + 1
    if len(prices.bars) < required:
        raise FactorInputError(
            f"Momentum over {lookback_periods} periods requires {required} bars, "
            f"got {len(prices.bars)}."
        )

    window = prices.bars[-required:]
    momentum = total_return(window[0].close, window[-1].close)

    return FactorResult(
        feature_id=feature_id,
        version=version,
        asset_id=prices.asset_id,
        value=momentum,
        timestamp=timestamp,
    )
