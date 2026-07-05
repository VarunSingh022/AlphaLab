"""Domain exceptions for the Risk Engine."""

from alphalab.common.exceptions import AlphaLabError


class RiskError(AlphaLabError):
    """Base exception for all Risk Engine errors."""


class RiskValidationError(RiskError):
    """Raised when an order request fails structural validation."""


class RiskConfigurationError(RiskError):
    """Raised when limits or configurations are invalid."""
