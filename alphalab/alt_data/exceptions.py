"""Domain exceptions for the Alternative Data Engine."""

from alphalab.common.exceptions import AlphaLabError


class AltDataError(AlphaLabError):
    """Base exception for all Alternative Data Engine errors."""


class AltDataInputError(AltDataError):
    """Raised when observation, score, or provenance inputs are invalid."""
