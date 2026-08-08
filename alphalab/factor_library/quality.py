"""Quality factor: return on book equity, a standard profitability measure."""

from decimal import Decimal

from alphalab.factor_library.exceptions import FactorComputationError
from alphalab.factor_library.inputs import FundamentalSnapshot
from alphalab.factor_library.result import FactorResult


def compute_quality(
    fundamentals: FundamentalSnapshot, feature_id: str, version: int, timestamp: float
) -> FactorResult:
    """Computes return on book equity (earnings_per_share / book_value_per_share).

    Higher values indicate the company generates more earnings per unit of book
    equity, a standard profitability-based quality proxy.

    Raises:
        FactorComputationError: If book_value_per_share is not positive, making the
            ratio undefined.
    """
    if fundamentals.book_value_per_share <= Decimal("0"):
        raise FactorComputationError(
            "Cannot compute return on book equity with non-positive book value: "
            f"{fundamentals.book_value_per_share}."
        )

    roe = float(fundamentals.earnings_per_share / fundamentals.book_value_per_share)

    return FactorResult(
        feature_id=feature_id,
        version=version,
        asset_id=fundamentals.asset_id,
        value=roe,
        timestamp=timestamp,
    )
