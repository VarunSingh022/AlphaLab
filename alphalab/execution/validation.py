"""Execution validation rules."""

from decimal import Decimal

from alphalab.execution.exceptions import ExecutionValidationError


def validate_execution_parameters(
    quantity: Decimal, price: Decimal, commission: Decimal, timestamp: float
) -> None:
    """Validates parameters for deterministic executions."""
    if quantity < Decimal("0"):
        raise ExecutionValidationError("Execution quantity cannot be negative.")
    if quantity == Decimal("0"):
        raise ExecutionValidationError("Execution quantity cannot be zero.")
    if price < Decimal("0"):
        raise ExecutionValidationError("Execution price cannot be negative.")
    if commission < Decimal("0"):
        raise ExecutionValidationError("Commission cannot be negative.")
    if timestamp < 0:
        raise ExecutionValidationError("Invalid timestamp.")
