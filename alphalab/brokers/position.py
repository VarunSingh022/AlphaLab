"""Immutable models defining portfolio holdings per broker."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto


class AssetClass(Enum):
    """Standardized asset classifications."""

    EQUITY = auto()
    FUTURE = auto()
    OPTION = auto()
    FOREX = auto()
    CRYPTO = auto()


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Immutable representation of an open asset position at a broker."""

    position_id: str
    account_id: str
    symbol: str
    asset_class: AssetClass
    quantity: Decimal
    average_price: Decimal
    market_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
