"""Domain exceptions for the Macro Engine."""

from alphalab.common.exceptions import AlphaLabError


class MacroError(AlphaLabError):
    """Base exception for all Macro Engine errors."""


class MacroInputError(MacroError):
    """Raised when indicator, curve, or rate inputs are invalid or incomplete."""


class MacroComputationError(MacroError):
    """Raised when a computation cannot produce a defined result."""
