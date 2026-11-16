"""The econometrics package's exceptions (v4.3, ADR-0074)."""

from alphalab.common.exceptions import AlphaLabError

__all__ = ["EconometricsError", "EconometricsInputError"]


class EconometricsError(AlphaLabError):
    """Base exception for every econometrics error."""


class EconometricsInputError(EconometricsError):
    """A series, a choice or a model an estimator cannot take, refused before it runs."""
