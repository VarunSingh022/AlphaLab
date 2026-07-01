"""Domain exceptions for the Risk Engine."""


class RiskError(Exception):
    """Base exception for all Risk Engine errors."""


class RiskValidationError(RiskError):
    """Raised when an order request fails structural validation."""


class RiskConfigurationError(RiskError):
    """Raised when limits or configurations are invalid."""
