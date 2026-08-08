"""Liquidity factor: average dollar volume over a lookback window."""

from decimal import Decimal

from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.inputs import PriceSeries
from alphalab.factor_library.result import FactorResult


def compute_liquidity(
    prices: PriceSeries, feature_id: str, version: int, lookback_periods: int, timestamp: float
) -> FactorResult:
    """Computes mean dollar volume (close * volume) over the lookback window.

    Higher values indicate an asset trades with greater dollar liquidity.

    Raises:
        FactorInputError: If fewer than `lookback_periods` bars are available, or
            lookback_periods is not positive.
    """
    if lookback_periods <= 0:
        raise FactorInputError(f"lookback_periods must be positive, got {lookback_periods}.")

    if len(prices.bars) < lookback_periods:
        raise FactorInputError(
            f"Liquidity over {lookback_periods} periods requires {lookback_periods} bars, "
            f"got {len(prices.bars)}."
        )

    window = prices.bars[-lookback_periods:]
    dollar_volumes = tuple(bar.close * bar.volume for bar in window)
    mean_dollar_volume = sum(dollar_volumes, Decimal("0")) / len(dollar_volumes)

    return FactorResult(
        feature_id=feature_id,
        version=version,
        asset_id=prices.asset_id,
        value=float(mean_dollar_volume),
        timestamp=timestamp,
    )
