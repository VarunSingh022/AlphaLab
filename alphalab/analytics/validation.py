"""Validation rules for ensuring clean analytical inputs."""

import math
from decimal import Decimal

from alphalab.analytics.exceptions import AnalyticsValidationError


def validate_returns(returns: tuple[float, ...]) -> None:
    """Checks a sequence of returns for invalid numerical states."""
    if not returns:
        raise AnalyticsValidationError("Return series cannot be empty.")

    for r in returns:
        if math.isnan(r):
            raise AnalyticsValidationError("Return series contains NaN values.")
        if math.isinf(r):
            raise AnalyticsValidationError("Return series contains Infinity values.")


def validate_capital(capital: Decimal) -> None:
    """Ensures capital amounts are valid for geometric calculations."""
    if capital < Decimal("0"):
        raise AnalyticsValidationError(f"Negative capital detected: {capital}")
    if capital.is_nan() or capital.is_infinite():
        raise AnalyticsValidationError("Capital cannot be NaN or Infinity.")
