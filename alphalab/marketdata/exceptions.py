"""Domain exceptions for Market Data Infrastructure."""

from alphalab.common.exceptions import AlphaLabError


class MarketDataError(AlphaLabError):
    """Base exception for all Market Data errors."""


class MarketDataValidationError(MarketDataError):
    """Raised when providers, subscriptions, or data fail validation."""


class InvalidMarketStateError(MarketDataError):
    """Raised when an illegal lifecycle transition is attempted."""
