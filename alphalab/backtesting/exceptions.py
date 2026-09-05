"""Domain exceptions for the Backtesting Engine."""

from alphalab.common.exceptions import AlphaLabError
from alphalab.market.exceptions import UnsupportedRecordError


class BacktestError(AlphaLabError):
    """Base exception for all Backtesting Engine errors."""


class DatasetValidationError(BacktestError):
    """Raised when a market dataset is empty, unordered, or ambiguous."""


__all__ = ["BacktestError", "DatasetValidationError", "UnsupportedRecordError"]
