"""Immutable definitions for asset classifications."""

from enum import Enum, auto


class DataAssetClass(Enum):
    EQUITY = auto()
    ETF = auto()
    FUTURE = auto()
    OPTION = auto()
    FOREX = auto()
    CRYPTO = auto()
    FUNDAMENTAL = auto()
    ECONOMIC = auto()
    ALTERNATIVE = auto()
