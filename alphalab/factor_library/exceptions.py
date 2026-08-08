"""Domain exceptions for the Factor Library."""

from alphalab.common.exceptions import AlphaLabError


class FactorLibraryError(AlphaLabError):
    """Base exception for all Factor Library errors."""


class FactorInputError(FactorLibraryError):
    """Raised when input data is insufficient or malformed for a computation."""


class FactorComputationError(FactorLibraryError):
    """Raised when a factor computation cannot produce a defined result."""
