"""Domain exceptions for the AlphaLab Workbench."""

from alphalab.common.exceptions import AlphaLabError


class WorkbenchError(AlphaLabError):
    """Base exception for all Workbench errors."""


class WorkbenchValidationError(WorkbenchError):
    """Raised when layouts, tabs, or projects fail structural validation."""


class InvalidWorkbenchStateError(WorkbenchError):
    """Raised when an illegal UI lifecycle transition is attempted."""
