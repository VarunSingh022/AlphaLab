"""Carry factor: dividend yield."""

from decimal import Decimal

from alphalab.factor_library.exceptions import FactorComputationError
from alphalab.factor_library.inputs import FundamentalSnapshot
from alphalab.factor_library.result import FactorResult


def compute_carry(
    fundamentals: FundamentalSnapshot, feature_id: str, version: int, timestamp: float
) -> FactorResult:
    """Computes trailing dividend yield (dividend_per_share / price).

    Raises:
        FactorComputationError: If price is not positive, making the ratio
            undefined.
    """
    if fundamentals.price <= Decimal("0"):
        raise FactorComputationError(
            f"Cannot compute dividend yield with non-positive price: {fundamentals.price}."
        )

    dividend_yield = float(fundamentals.dividend_per_share / fundamentals.price)

    return FactorResult(
        feature_id=feature_id,
        version=version,
        asset_id=fundamentals.asset_id,
        value=dividend_yield,
        timestamp=timestamp,
    )
