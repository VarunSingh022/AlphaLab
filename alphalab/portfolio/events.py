from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PortfolioEvent:
    timestamp: float
    account_id: str


@dataclass(frozen=True, slots=True)
class CashDeposited(PortfolioEvent):
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class CashWithdrawn(PortfolioEvent):
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class PositionOpened(PortfolioEvent):
    asset_id: str
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class PositionIncreased(PortfolioEvent):
    asset_id: str
    added_quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class PositionReduced(PortfolioEvent):
    asset_id: str
    reduced_quantity: Decimal
    price: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PositionClosed(PortfolioEvent):
    asset_id: str
    price: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class MarketValueUpdated(PortfolioEvent):
    prices: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class PortfolioValuationUpdated(PortfolioEvent):
    nav: Decimal
