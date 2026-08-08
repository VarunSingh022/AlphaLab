"""Descriptive catalog of every factor this library can compute.

This is deliberately not a stateful registry -- registration, versioning, and
lifecycle management belong to `alphalab.feature_store.registry.FeatureRegistry`.
This catalog exists only so a caller can discover what's available and which input
shape each factor requires, without importing every compute_* function by hand.
"""

from dataclasses import dataclass
from enum import Enum, auto


class FactorCategory(Enum):
    """Standard quantitative factor style classifications."""

    MOMENTUM = auto()
    VALUE = auto()
    QUALITY = auto()
    CARRY = auto()
    VOLATILITY = auto()
    LIQUIDITY = auto()


class RequiredInput(Enum):
    """Which input shape a factor's compute function expects."""

    PRICE_SERIES = auto()
    FUNDAMENTAL_SNAPSHOT = auto()


@dataclass(frozen=True, slots=True)
class FactorSpec:
    """Describes a single available factor without importing its implementation."""

    factor_id: str
    category: FactorCategory
    required_input: RequiredInput
    description: str


FACTOR_CATALOG: tuple[FactorSpec, ...] = (
    FactorSpec(
        factor_id="momentum",
        category=FactorCategory.MOMENTUM,
        required_input=RequiredInput.PRICE_SERIES,
        description="Trailing total return over a lookback window of bars.",
    ),
    FactorSpec(
        factor_id="volatility",
        category=FactorCategory.VOLATILITY,
        required_input=RequiredInput.PRICE_SERIES,
        description="Annualized standard deviation of returns over a lookback window.",
    ),
    FactorSpec(
        factor_id="liquidity",
        category=FactorCategory.LIQUIDITY,
        required_input=RequiredInput.PRICE_SERIES,
        description="Mean dollar volume over a lookback window.",
    ),
    FactorSpec(
        factor_id="value",
        category=FactorCategory.VALUE,
        required_input=RequiredInput.FUNDAMENTAL_SNAPSHOT,
        description="Earnings yield (earnings_per_share / price).",
    ),
    FactorSpec(
        factor_id="quality",
        category=FactorCategory.QUALITY,
        required_input=RequiredInput.FUNDAMENTAL_SNAPSHOT,
        description="Return on book equity (earnings_per_share / book_value_per_share).",
    ),
    FactorSpec(
        factor_id="carry",
        category=FactorCategory.CARRY,
        required_input=RequiredInput.FUNDAMENTAL_SNAPSHOT,
        description="Trailing dividend yield (dividend_per_share / price).",
    ),
)


def get_spec(factor_id: str) -> FactorSpec | None:
    """Looks up a factor's descriptive spec by id, or returns None if unknown."""
    for spec in FACTOR_CATALOG:
        if spec.factor_id == factor_id:
            return spec
    return None
