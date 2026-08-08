"""Immutable option Greeks record."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Greeks:
    """First and second-order price sensitivities for an option contract.

    Attributes:
        delta: Sensitivity to a $1 change in the underlying's price.
        gamma: Sensitivity of delta to a $1 change in the underlying's price.
        theta: Sensitivity to one day of time decay (already divided by 365).
        vega: Sensitivity to a 1.0 (100 percentage point) change in volatility.
        rho: Sensitivity to a 1.0 (100 percentage point) change in the risk-free
            rate.
    """

    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
