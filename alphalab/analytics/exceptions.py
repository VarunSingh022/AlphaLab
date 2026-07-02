"""Domain exceptions for the Analytics Engine."""


class AnalyticsError(Exception):
    """Base exception for all Analytics Engine errors."""

    pass


class AnalyticsValidationError(AnalyticsError):
    """Raised when data provided to the Analytics Engine is invalid."""

    pass
