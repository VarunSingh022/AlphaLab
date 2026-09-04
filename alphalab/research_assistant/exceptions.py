"""Domain exceptions for the Research Assistant."""

from alphalab.common.exceptions import AlphaLabError


class ResearchAssistantError(AlphaLabError):
    """Base exception for all Research Assistant errors."""


class ResearchAssistantInputError(ResearchAssistantError):
    """Raised when generation, evaluation, or workflow inputs are invalid."""
