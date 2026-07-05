"""Domain exceptions for the Broker Integration Framework."""

from alphalab.common.exceptions import AlphaLabError


class IntegrationError(AlphaLabError):
    """Base exception for all integration operations."""


class IntegrationValidationError(IntegrationError):
    """Raised when integration configurations or payloads fail structural validation."""


class AuthenticationError(IntegrationError):
    """Raised when broker credentials fail validation or expire."""


class ConnectionManagerError(IntegrationError):
    """Raised when an illegal connection state transition is attempted."""
