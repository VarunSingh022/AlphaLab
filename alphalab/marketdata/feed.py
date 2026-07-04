"""Canonical Market Data Payload definitions."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    timestamp: float
    bid: float
    ask: float
    bid_size: float
    ask_size: float


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    timestamp: float
    price: float
    size: float


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(frozen=True, slots=True)
class OrderBook:
    symbol: str
    timestamp: float
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
