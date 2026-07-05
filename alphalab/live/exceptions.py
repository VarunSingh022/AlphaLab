"""Domain exceptions for the Live Market Data infrastructure."""

from alphalab.common.exceptions import AlphaLabError


class LiveDataError(AlphaLabError):
    """Base exception for all Live Market Data errors."""


class LiveValidationError(LiveDataError):
    """Raised when providers, subscriptions, or ticks fail structural validation."""


class InvalidLiveStateError(LiveDataError):
    """Raised when an illegal state transition is attempted."""
