"""Domain exceptions for the Options Engine."""

from alphalab.common.exceptions import AlphaLabError


class OptionsError(AlphaLabError):
    """Base exception for all Options Engine errors."""


class OptionInputError(OptionsError):
    """Raised when option contract or market inputs are invalid or out of range."""


class OptionPricingError(OptionsError):
    """Raised when a pricing or Greeks computation cannot produce a defined result."""
