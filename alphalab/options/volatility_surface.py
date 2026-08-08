"""Implied volatility surface: strike/expiry-indexed implied vol observations.

This is deliberately a simple model: exact (strike, expiry) match, or linear
interpolation between the two nearest strikes at a matching expiry. It does not fit
a full 2D surface (e.g. SVI, SABR) or interpolate across expiries -- AlphaLab has no
numpy/scipy dependency to build that on top of, and a from-scratch numerical fit is
out of scope here. `implied_vol_at` returns None rather than extrapolating when a
strike falls outside the observed range or no data exists for the requested expiry.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VolPoint:
    """A single implied volatility observation.

    Attributes:
        strike: Strike price the observation applies to.
        expiry: Unix timestamp of the expiry the observation applies to.
        implied_vol: Observed implied volatility, e.g. 0.25 for 25%.
    """

    strike: float
    expiry: float
    implied_vol: float


@dataclass(frozen=True, slots=True)
class VolatilitySurface:
    """An immutable collection of implied volatility observations for one underlying.

    Attributes:
        underlying_asset_id: Identifier of the underlying asset.
        timestamp: Unix timestamp this surface is as-of.
        points: Every observation, in no particular order.
    """

    underlying_asset_id: str
    timestamp: float
    points: tuple[VolPoint, ...]


def implied_vol_at(surface: VolatilitySurface, strike: float, expiry: float) -> float | None:
    """Looks up implied volatility for a strike/expiry pair.

    Returns an exact match if present, otherwise linearly interpolates between the
    two nearest strikes at the same expiry. Returns None if no observations exist
    for the given expiry, or if the strike falls outside the observed strike range
    at that expiry (no extrapolation).
    """
    same_expiry = sorted((p for p in surface.points if p.expiry == expiry), key=lambda p: p.strike)
    if not same_expiry:
        return None

    for point in same_expiry:
        if point.strike == strike:
            return point.implied_vol

    if strike < same_expiry[0].strike or strike > same_expiry[-1].strike:
        return None

    lower = max((p for p in same_expiry if p.strike < strike), key=lambda p: p.strike)
    upper = min((p for p in same_expiry if p.strike > strike), key=lambda p: p.strike)

    weight = (strike - lower.strike) / (upper.strike - lower.strike)
    return lower.implied_vol + weight * (upper.implied_vol - lower.implied_vol)
