"""Domain exceptions for the Research Engine."""

from alphalab.common.exceptions import AlphaLabError


class ResearchError(AlphaLabError):
    """Base exception for all Research Engine errors."""


class ResearchValidationError(ResearchError):
    """Raised when research data payloads fail validation."""


class InvalidResearchStateError(ResearchError):
    """Raised when an illegal lifecycle transition is attempted."""
