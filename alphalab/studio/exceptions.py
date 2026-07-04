"""Domain exceptions for Strategy Studio."""

class StudioError(Exception):
    """Base exception for all Strategy Studio errors."""

class StudioValidationError(StudioError):
    """Raised when projects, pipelines, or workspaces fail validation."""

class InvalidStudioStateError(StudioError):
    """Raised when an illegal lifecycle transition is attempted in the workspace."""