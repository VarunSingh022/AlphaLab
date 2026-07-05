"""Domain exceptions for the Analytics Engine."""

from alphalab.common.exceptions import AlphaLabError


class AnalyticsError(AlphaLabError):
    """Base exception for all Analytics Engine errors."""

    pass


class AnalyticsValidationError(AnalyticsError):
    """Raised when data provided to the Analytics Engine is invalid."""

    pass
