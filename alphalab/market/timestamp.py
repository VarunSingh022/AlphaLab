"""Strongly typed timestamp utilities for deterministic time handling."""


def to_unix_seconds(milliseconds: int) -> float:
    """Converts unix milliseconds to floating point unix seconds."""
    return milliseconds / 1000.0


def to_unix_milliseconds(seconds: float) -> int:
    """Converts floating point unix seconds to integer unix milliseconds."""
    return int(seconds * 1000)


def is_valid_timestamp(timestamp: float) -> bool:
    """Validates that a timestamp is strictly positive."""
    return timestamp > 0.0
