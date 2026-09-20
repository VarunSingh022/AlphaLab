"""Domain exceptions for market conventions."""

from alphalab.common.exceptions import AlphaLabError


class ConventionError(AlphaLabError):
    """Base exception for every market-convention error."""


class ConventionInputError(ConventionError):
    """Raised when a declared convention is incomplete or self-contradictory."""


class ConventionViolationError(ConventionError):
    """Raised when a value does not satisfy the convention it was measured against.

    Distinct from :class:`ConventionInputError`, which says the *convention* is
    wrong. This one says the convention is fine and the price, quantity or date
    handed to it is not expressible under it -- a price off the tick grid, a
    quantity that is not a whole number of lots, a settlement date the calendar
    cannot reach.
    """
