"""Domain exceptions for the Market Data Feed Layer."""


class FeedError(Exception):
    """Base exception for all Market Data Feed errors."""

    pass


class FeedValidationError(FeedError):
    """Raised when feed configuration or data fails validation."""

    pass


class InvalidFeedStateError(FeedError):
    """Raised when an invalid connection or subscription transition is attempted."""

    pass


class FeedConnectionError(FeedError):
    """Raised when connection lifecycle operations fail."""

    pass
