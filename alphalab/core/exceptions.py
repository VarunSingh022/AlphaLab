"""Core domain exceptions."""


class AlphaLabCoreError(Exception):
    """Base exception for all core domain errors."""


class DomainValidationError(AlphaLabCoreError, ValueError):
    """Raised when a domain model receives invalid constructor arguments."""
