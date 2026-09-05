"""Domain exceptions for the Market Data Engine."""

from alphalab.common.exceptions import AlphaLabError


class MarketDataError(AlphaLabError):
    """Base exception for all Market Data Engine errors."""

    pass


class MarketValidationError(MarketDataError):
    """Raised when market data fails business invariant validation."""

    pass


class UnsupportedRecordError(MarketDataError):
    """Raised when a record carries a market input nothing can publish.

    Defined here rather than in :mod:`alphalab.backtesting`, where it started:
    four environments now publish records through one canonical step, so an
    unpublishable input is a market-layer fact, not a backtest-specific one.
    ``alphalab.backtesting.exceptions.UnsupportedRecordError`` is this class.
    """

    pass
