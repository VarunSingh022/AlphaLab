"""Immutable exposure tracking models."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PortfolioExposure:
    """Immutable snapshot of categorical exposures."""

    portfolio_id: str
    timestamp: float
    sector_exposure: Mapping[str, float] = field(default_factory=dict)
    country_exposure: Mapping[str, float] = field(default_factory=dict)
    factor_exposure: Mapping[str, float] = field(default_factory=dict)
    net_beta: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
