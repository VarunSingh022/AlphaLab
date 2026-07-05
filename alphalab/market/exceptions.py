"""Domain exceptions for the Market Data Engine."""

from alphalab.common.exceptions import AlphaLabError


class MarketDataError(AlphaLabError):
    """Base exception for all Market Data Engine errors."""

    pass


class MarketValidationError(MarketDataError):
    """Raised when market data fails business invariant validation."""

    pass
