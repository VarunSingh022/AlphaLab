"""Shared exception hierarchy for AlphaLab."""


class AlphaLabError(Exception):
    """Base exception for shared AlphaLab errors."""


class AlphaLabValidationError(AlphaLabError, ValueError):
    """Raised when shared validation rules fail."""


class AlphaLabSerializationError(AlphaLabError):
    """Raised when shared serialization helpers cannot encode a value."""


class AlphaLabRegistryError(AlphaLabError):
    """Raised when shared registry operations fail."""
