"""Immutable definitions for asset classifications and symbols."""

from dataclasses import dataclass
from enum import Enum, auto


class AssetClass(Enum):
    EQUITY = auto()
    ETF = auto()
    FUTURE = auto()
    OPTION = auto()
    FOREX = auto()
    CRYPTO = auto()


@dataclass(frozen=True, slots=True)
class SymbolMetadata:
    symbol: str
    asset_class: AssetClass
    exchange: str
    currency: str
