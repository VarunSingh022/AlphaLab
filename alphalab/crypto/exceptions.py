"""Domain exceptions for the Crypto Engine."""

from alphalab.common.exceptions import AlphaLabError


class CryptoError(AlphaLabError):
    """Base exception for all Crypto Engine errors."""


class CryptoInputError(CryptoError):
    """Raised when instrument, funding, or symbol inputs are invalid."""


class CryptoComputationError(CryptoError):
    """Raised when a computation cannot produce a defined result."""
