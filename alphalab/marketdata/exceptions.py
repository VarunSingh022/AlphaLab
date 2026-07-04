"""Domain exceptions for Market Data Infrastructure."""

class MarketDataError(Exception):
    """Base exception for all Market Data errors."""

class MarketDataValidationError(MarketDataError):
    """Raised when providers, subscriptions, or data fail validation."""

class InvalidMarketStateError(MarketDataError):
    """Raised when an illegal lifecycle transition is attempted."""