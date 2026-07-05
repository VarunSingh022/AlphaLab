"""Domain exceptions for the Plugin SDK."""

from alphalab.common.exceptions import AlphaLabError


class PluginError(AlphaLabError):
    """Base exception for all Plugin SDK errors."""


class PluginValidationError(PluginError):
    """Raised when a plugin fails structural or metadata validation."""


class InvalidPluginStateError(PluginError):
    """Raised when an illegal lifecycle transition is attempted on a plugin."""
