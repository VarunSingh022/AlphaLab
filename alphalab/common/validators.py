"""Shared validation helpers."""

from collections.abc import Mapping

from alphalab.common.exceptions import AlphaLabValidationError


def require_non_empty_string(
    value: str,
    field_name: str,
    *,
    message: str | None = None,
    exception_type: type[Exception] = AlphaLabValidationError,
) -> str:
    """Validate and return a non-empty string.

    Args:
        value: Candidate value.
        field_name: Name used in the validation error.
        message: Optional exact validation message.
        exception_type: Exception type raised on validation failure.

    Returns:
        The validated string.

    Raises:
        Exception: If the value is empty or whitespace.
    """

    if not value.strip():
        raise exception_type(message or f"{field_name} cannot be empty")
    return value


def require_mapping_key[KeyT](
    mapping: Mapping[KeyT, object],
    key: KeyT,
    message: str,
    *,
    exception_type: type[Exception] = AlphaLabValidationError,
) -> None:
    """Validate that a mapping contains a key."""

    if key not in mapping:
        raise exception_type(message)


def require_missing_mapping_key[KeyT](
    mapping: Mapping[KeyT, object],
    key: KeyT,
    message: str,
    *,
    exception_type: type[Exception] = AlphaLabValidationError,
) -> None:
    """Validate that a mapping does not contain a key."""

    if key in mapping:
        raise exception_type(message)


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
