"""Value factor: earnings yield, a standard inverse-valuation measure."""

from decimal import Decimal

from alphalab.factor_library.exceptions import FactorComputationError
from alphalab.factor_library.inputs import FundamentalSnapshot
from alphalab.factor_library.result import FactorResult


def compute_value(
    fundamentals: FundamentalSnapshot, feature_id: str, version: int, timestamp: float
) -> FactorResult:
    """Computes earnings yield (earnings_per_share / price).

    Earnings yield is the inverse of the P/E ratio -- higher values indicate a
    cheaper (more "value") asset relative to its earnings.

    Raises:
        FactorComputationError: If price is not positive, making the ratio
            undefined.
    """
    if fundamentals.price <= Decimal("0"):
        raise FactorComputationError(
            f"Cannot compute earnings yield with non-positive price: {fundamentals.price}."
        )

    earnings_yield = float(fundamentals.earnings_per_share / fundamentals.price)

    return FactorResult(
        feature_id=feature_id,
        version=version,
        asset_id=fundamentals.asset_id,
        value=earnings_yield,
        timestamp=timestamp,
    )
