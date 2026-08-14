"""Domain exceptions for the Reinforcement Learning Engine."""

from alphalab.common.exceptions import AlphaLabError


class RLError(AlphaLabError):
    """Base exception for all Reinforcement Learning Engine errors."""


class RLInputError(RLError):
    """Raised when environment, action, or trajectory inputs are invalid."""
