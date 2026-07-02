"""Immutable StrategyContext providing a read-only window into the world."""

from dataclasses import dataclass
from typing import Any, Protocol


class PortfolioSnapshotProtocol(Protocol):
    """Read-only view of portfolio state."""


class MarketViewProtocol(Protocol):
    """Read-only view of current market data."""


class ClockProtocol(Protocol):
    """Virtual or monotonic clock source."""

    def now(self) -> float: ...


class ScopedLoggerProtocol(Protocol):
    """Structured, strategy-scoped write-only logger."""

    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


class RiskViewProtocol(Protocol):
    """Read-only view of current risk limits and exposure."""


class OrderFacadeProtocol(Protocol):
    """Constrained facade for order queries, NOT for direct placement."""


class HistoryAccessorProtocol(Protocol):
    """Clock-bounded accessor for historical data."""


class UniverseProtocol(Protocol):
    """Read-only registry of tradable instruments for this strategy."""


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """
    The single object through which a strategy observes the outside world.
    Constructed or pooled per hook invocation. Purely immutable and read-only.
    """

    portfolio: PortfolioSnapshotProtocol
    market: MarketViewProtocol
    clock: ClockProtocol
    logger: ScopedLoggerProtocol
    risk_view: RiskViewProtocol
    config: Any
    orders: OrderFacadeProtocol
    history: HistoryAccessorProtocol
    universe: UniverseProtocol
