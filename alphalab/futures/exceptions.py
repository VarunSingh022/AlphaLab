"""Domain exceptions for the Futures Engine."""

from alphalab.common.exceptions import AlphaLabError


class FuturesError(AlphaLabError):
    """Base exception for all Futures Engine errors."""


class FuturesInputError(FuturesError):
    """Raised when contract, roll, or curve inputs are invalid or incomplete."""


class FuturesComputationError(FuturesError):
    """Raised when a computation cannot produce a defined result."""
