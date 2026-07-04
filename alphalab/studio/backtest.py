"""Immutable configurations for tracked backtests."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BacktestConfiguration:
    backtest_id: str
    strategy_id: str
    dataset_ids: tuple[str, ...]
    start_timestamp: float
    end_timestamp: float
    initial_capital: float