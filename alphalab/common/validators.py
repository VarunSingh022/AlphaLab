"""Shared validation helpers."""

from alphalab.common.exceptions import AlphaLabValidationError


def require_non_empty_string(value: str, field_name: str) -> str:
    """Validate and return a non-empty string.

    Args:
        value: Candidate value.
        field_name: Name used in the validation error.

    Returns:
        The validated string.

    Raises:
        AlphaLabValidationError: If the value is empty or whitespace.
    """

    if not value.strip():
        raise AlphaLabValidationError(f"{field_name} cannot be empty")
    return value


def require_positive_int(value: int, field_name: str) -> int:
    """Validate and return a positive integer."""

    if value <= 0:
        raise AlphaLabValidationError(f"{field_name} must be positive")
    return value


def require_non_negative_int(value: int, field_name: str) -> int:
    """Validate and return a non-negative integer."""

    if value < 0:
        raise AlphaLabValidationError(f"{field_name} must be non-negative")
    return value


def require_type[T](value: object, expected_type: type[T], field_name: str) -> T:
    """Validate and return a value with the expected runtime type."""

    if not isinstance(value, expected_type):
        raise AlphaLabValidationError(f"{field_name} must be {expected_type.__name__}")
    return value
