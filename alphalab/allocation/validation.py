"""Validation rules ensuring structural integrity of allocations."""

from decimal import Decimal

from alphalab.allocation.exceptions import AllocationValidationError
from alphalab.strategy.events import Intent


def validate_intent(intent: Intent) -> None:
    """Ensures intent structural integrity before processing."""
    if not intent.strategy_id:
        raise AllocationValidationError("Intent must have a valid strategy_id.")
    if not intent.instrument:
        raise AllocationValidationError("Intent must have a valid instrument.")
    if intent.target.is_nan():
        raise AllocationValidationError("Intent target cannot be NaN.")
    if intent.strength < Decimal("0.0") or intent.strength > Decimal("1.0"):
        raise AllocationValidationError("Intent strength must be between 0.0 and 1.0.")
    if intent.timestamp < 0.0:
        raise AllocationValidationError("Intent timestamp cannot be negative.")


def validate_net_quantity(quantity: Decimal, enforce_long_only: bool = False) -> None:
    """Ensures netted quantities are valid."""
    if quantity.is_nan():
        raise AllocationValidationError("Net quantity cannot be NaN.")
    if enforce_long_only and quantity < Decimal("0.00"):
        raise AllocationValidationError("Negative allocation rejected: long-only enforced.")
