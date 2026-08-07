"""Immutable domain models representing the institutional state engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.position import Position

PositionState = Position

__all__ = [
    "BaseState",
    "MarketState",
    "PortfolioState",
    "PositionState",
    "SystemState",
]


@dataclass(frozen=True, slots=True)
class BaseState:
    """Base immutable state primitive requiring versioning and audit timestamps."""

    version: int = 0
    timestamp: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MarketState(BaseState):
    """Immutable snapshot of live or historical market observations."""

    prices: Mapping[str, float] = field(default_factory=dict)
    market_data: Mapping[str, Any] = field(default_factory=dict)
    calendar_status: str = "CLOSED"


def _default_portfolio_state() -> PortfolioState:
    """Creates the default empty canonical portfolio snapshot for kernel state."""
    return PortfolioState(
        account=Account(
            account_id="kernel-default-account",
            base_currency="USD",
            name="Kernel Default Account",
            created_at=0.0,
        )
    )


@dataclass(frozen=True, slots=True)
class SystemState(BaseState):
    """Root immutable state tree aggregating all subsystem states."""

    market: MarketState = field(default_factory=MarketState)
    portfolio: PortfolioState = field(default_factory=_default_portfolio_state)
    configuration: Mapping[str, Any] = field(default_factory=dict)
