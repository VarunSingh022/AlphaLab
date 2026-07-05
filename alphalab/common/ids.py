"""Shared identifier helpers."""

from typing import NewType
from uuid import UUID, uuid4

from alphalab.common.exceptions import AlphaLabValidationError

Identifier = NewType("Identifier", str)


def new_id() -> Identifier:
    """Return a new UUID4-backed identifier."""

    return Identifier(str(uuid4()))


def is_uuid(value: str) -> bool:
    """Return whether a string is a valid UUID."""

    try:
        UUID(value)
    except ValueError:
        return False
    return True


def require_uuid(value: str, field_name: str) -> Identifier:
    """Validate and return a UUID-backed identifier."""

    if not value or not is_uuid(value):
        raise AlphaLabValidationError(f"{field_name} must be a valid UUID")
    return Identifier(value)
