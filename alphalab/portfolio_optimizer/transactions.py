"""Immutable models defining intended allocation shifts."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TargetTransaction:
    """Immutable definition of a portfolio weight transition."""

    symbol: str
    current_weight: float
    target_weight: float
    trade_weight: float
    estimated_cost: float
