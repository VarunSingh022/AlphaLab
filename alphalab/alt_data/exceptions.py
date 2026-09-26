"""Domain exceptions for the Alternative Data Engine."""

from alphalab.common.exceptions import AlphaLabError


class AltDataError(AlphaLabError):
    """Base exception for all Alternative Data Engine errors."""


class AltDataInputError(AltDataError):
    """Raised when observation, score, or provenance inputs are invalid."""


class PointInTimeError(AltDataError):
    """Raised when a request would read what was not knowable at its instant.

    Separate from :class:`AltDataInputError` because the two say different
    things: the inputs were malformed, versus they were well formed and the
    information asked for did not exist yet -- or its availability was never
    established, so it cannot be shown to have existed.
    """
