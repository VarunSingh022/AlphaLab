"""Immutable definitions for portfolio weight vectors."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TargetWeights:
    """Immutable mapping of assets to their target fractional weights."""

    portfolio_id: str
    timestamp: float
    weights: Mapping[str, float] = field(default_factory=dict)
