"""Constraint definitions for optimization and normalization."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class AllocationConstraints:
    """Limits and bounds applied during normalization."""

    max_weight_per_asset: Decimal = Decimal("1.0")
    min_weight_per_asset: Decimal = Decimal("-1.0")
    allow_shorting: bool = True
    enforce_integer_quantities: bool = False
