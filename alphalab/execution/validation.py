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
    # A price's sign is the instrument's question and a commission is signed --
    # a negative one is a rebate -- since v3.11 (ACC-007).
    if not price.is_finite():
        raise ExecutionValidationError("Execution price must be a finite number.")
    if not commission.is_finite():
        raise ExecutionValidationError("Commission must be a finite number.")
    if timestamp < 0:
        raise ExecutionValidationError("Invalid timestamp.")
