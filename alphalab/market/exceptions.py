"""Domain exceptions for the Market Data Engine."""


class MarketDataError(Exception):
    """Base exception for all Market Data Engine errors."""

    pass


class MarketValidationError(MarketDataError):
    """Raised when market data fails business invariant validation."""

    pass
