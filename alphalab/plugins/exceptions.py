"""Domain exceptions for the Plugin SDK."""


class PluginError(Exception):
    """Base exception for all Plugin SDK errors."""


class PluginValidationError(PluginError):
    """Raised when a plugin fails structural or metadata validation."""


class InvalidPluginStateError(PluginError):
    """Raised when an illegal lifecycle transition is attempted on a plugin."""
