"""Domain exceptions for the Reporting Layer."""

from alphalab.common.exceptions import AlphaLabError


class ReportingError(AlphaLabError):
    """Base exception for all Reporting Engine errors."""


class ReportingValidationError(ReportingError):
    """Raised when report definitions or contents fail structural validation."""


class ExportError(ReportingError):
    """Raised when a deterministic export operation fails."""
