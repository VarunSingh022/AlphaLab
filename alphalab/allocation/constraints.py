"""Constraint definitions for optimization and normalization."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class AllocationConstraints:
    """Limits and bounds applied during normalization.

    Attributes:
        max_weight_per_asset: Upper weight bound for normalization.
        min_weight_per_asset: Lower weight bound for normalization.
        allow_shorting: ``False`` is long-only: an order that would leave the
            asset's committed position (filled plus working orders) below zero is
            refused, and one that closes a long passes. The positions come from
            the caller -- see ``AllocationEngine.allocate(positions=...)``;
            without them every sale is refused, since a delta alone cannot tell
            a close from a short.
        enforce_integer_quantities: Round each netted order to the nearest whole
            unit (ties to even) before any check reads it; an order that rounds
            to zero is not emitted.
    """

    max_weight_per_asset: Decimal = Decimal("1.0")
    min_weight_per_asset: Decimal = Decimal("-1.0")
    allow_shorting: bool = True
    enforce_integer_quantities: bool = False
