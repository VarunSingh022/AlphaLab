"""Shared time helpers."""

from datetime import UTC, datetime

from alphalab.common.exceptions import AlphaLabValidationError


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(UTC)


def ensure_timezone_aware(value: datetime, field_name: str = "timestamp") -> datetime:
    """Validate and return a timezone-aware datetime."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise AlphaLabValidationError(f"{field_name} must be timezone-aware")
    return value


def to_utc(value: datetime) -> datetime:
    """Convert a timezone-aware datetime to UTC."""

    return ensure_timezone_aware(value).astimezone(UTC)
