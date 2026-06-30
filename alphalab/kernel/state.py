"""Immutable domain models representing the institutional state engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class BaseState:
    """Base immutable state primitive requiring versioning and audit timestamps."""

    version: int = 0
    timestamp: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PositionState:
    """Immutable representation of a single asset position."""

    symbol: str
    quantity: float
    average_price: float
    market_price: float

    @property
    def market_value(self) -> float:
        """Calculates current market value of the position."""
        return self.quantity * self.market_price

    @property
    def unrealized_pnl(self) -> float:
        """Calculates current open profit and loss."""
        return (self.market_price - self.average_price) * self.quantity


@dataclass(frozen=True, slots=True)
class MarketState(BaseState):
    """Immutable snapshot of live or historical market observations."""

    prices: Mapping[str, float] = field(default_factory=dict)
    market_data: Mapping[str, Any] = field(default_factory=dict)
    calendar_status: str = "CLOSED"


@dataclass(frozen=True, slots=True)
class PortfolioState(BaseState):
    """Immutable accounting snapshot for portfolio operations."""

    cash: float = 0.0
    positions: Mapping[str, PositionState] = field(default_factory=dict)
    realized_pnl: float = 0.0

    @property
    def equity(self) -> float:
        """Total account liquidation value."""
        positions_val = sum(pos.market_value for pos in self.positions.values())
        return self.cash + positions_val

    @property
    def unrealized_pnl(self) -> float:
        """Sum of unrealized PnL across all open positions."""
        return sum(pos.unrealized_pnl for pos in self.positions.values())


@dataclass(frozen=True, slots=True)
class SystemState(BaseState):
    """Root immutable state tree aggregating all subsystem states."""

    market: MarketState = field(default_factory=MarketState)
    portfolio: PortfolioState = field(default_factory=PortfolioState)
    configuration: Mapping[str, Any] = field(default_factory=dict)
