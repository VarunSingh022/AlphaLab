"""The econometrics package's exceptions (v4.3, ADR-0074)."""

import math

from alphalab.common.exceptions import AlphaLabError, ConvergenceError

__all__ = ["CollapsedStateError", "EconometricsError", "EconometricsInputError"]


class EconometricsError(AlphaLabError):
    """Base exception for every econometrics error."""


class EconometricsInputError(EconometricsError):
    """A series, a choice or a model an estimator cannot take, refused before it runs."""


class CollapsedStateError(ConvergenceError, EconometricsError):
    """A regime model's state collapsed, where the likelihood has no maximum (ADR-0075 section 3).

    A state whose variance falls to zero on its observations sends the
    likelihood to infinity, and a state left with no observation has no
    variance at all. Either is a fit that did not converge, refused with the
    state named and never floored. What it reached is the likelihood's
    growth, without bound.

    Attributes:
        state: The state, numbered from one as the fit numbered it when it
            collapsed, before the states are ordered.
        variance: Its variance when the fit stopped; not a number for a state
            left with no observation.
        iteration: The iteration it collapsed at.
    """

    def __init__(
        self,
        method: str,
        tolerance: float,
        limit: int,
        *,
        state: int,
        variance: float,
        iteration: int,
    ) -> None:
        super().__init__(method, tolerance, limit, math.inf)
        detail = (
            "is left with no observation"
            if math.isnan(variance)
            else f"has a variance of {variance!r}, where the likelihood is unbounded"
        )
        self.args = (
            f"{method}: at iteration {iteration}, state {state} {detail}. It is refused as a "
            f"fit that did not converge to {tolerance!r}, never floored: restate the start "
            "or the number of states.",
        )
        self.state = state
        self.variance = variance
        self.iteration = iteration
