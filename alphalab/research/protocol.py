"""Immutable interface protocol for Research data ingestion."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TradePayload:
    trade_id: str
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ResearchPayload:
    """Standardized immutable data structure required for research evaluation."""

    strategy_id: str
    returns: tuple[float, ...]
    trades: tuple[TradePayload, ...]
    parameters: dict[str, float]
    market_regimes: tuple[str, ...]
    aum: float


class ResearchProtocol(Protocol):
    """Pure functional interface for providing research data."""

    def get_research_payload(self) -> ResearchPayload: ...
