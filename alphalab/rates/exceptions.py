"""The rates package's exceptions (v4.3, ADR-0065)."""

from alphalab.common.exceptions import AlphaLabError

__all__ = ["BootstrapError", "CurveRangeError", "RatesError", "RatesInputError"]


class RatesError(AlphaLabError):
    """Base exception for every rates error."""


class RatesInputError(RatesError):
    """A curve, quote or choice the package cannot take, refused before anything is computed."""


class CurveRangeError(RatesError):
    """A read outside a curve: before its anchor, or past its last pillar unextrapolated."""


class BootstrapError(RatesError):
    """A quote no discount factor in the search range reprices: refused, never skipped."""
