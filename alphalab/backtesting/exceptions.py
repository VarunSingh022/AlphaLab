"""Domain exceptions for the Backtesting Engine."""

from alphalab.common.exceptions import AlphaLabError


class BacktestError(AlphaLabError):
    """Base exception for all Backtesting Engine errors."""


class DatasetValidationError(BacktestError):
    """Raised when a market dataset is empty, unordered, or ambiguous."""


class UnsupportedRecordError(BacktestError):
    """Raised when a dataset record is not a market input the engine can publish."""
