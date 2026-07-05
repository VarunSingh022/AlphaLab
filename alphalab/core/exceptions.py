"""Core domain exceptions."""

from alphalab.common.exceptions import AlphaLabError


class AlphaLabCoreError(AlphaLabError):
    """Base exception for all core domain errors."""


class DomainValidationError(AlphaLabCoreError, ValueError):
    """Raised when a domain model receives invalid constructor arguments."""
