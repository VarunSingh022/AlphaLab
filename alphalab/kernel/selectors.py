"""Pure deterministic query functions (selectors) for navigating immutable state hierarchies."""

from collections.abc import Mapping
from typing import Any

from alphalab.kernel.state import PositionState, SystemState


def get_cash(state: SystemState) -> float:
    """Returns the current cash balance."""
    return state.portfolio.cash


def get_positions(state: SystemState) -> Mapping[str, PositionState]:
    """Returns an immutable mapping of all portfolio positions."""
    return state.portfolio.positions


def get_equity(state: SystemState) -> float:
    """Returns total portfolio equity (cash + open positions market value)."""
    return state.portfolio.equity


def get_symbol_position(state: SystemState, symbol: str) -> PositionState | None:
    """Retrieves the exact position state for a given ticker symbol, or None if unassigned."""
    return state.portfolio.positions.get(symbol)


def get_portfolio_value(state: SystemState) -> float:
    """Returns total portfolio equity (alias for standardized accounting conventions)."""
    return state.portfolio.equity


def get_realized_pnl(state: SystemState) -> float:
    """Returns aggregate realized profit and loss."""
    return state.portfolio.realized_pnl


def get_unrealized_pnl(state: SystemState) -> float:
    """Returns aggregate open profit and loss across all positions."""
    return state.portfolio.unrealized_pnl


def get_market_price(state: SystemState, symbol: str) -> float | None:
    """Returns the most recent price observation for a specific instrument."""
    return state.market.prices.get(symbol)


def get_configuration_parameter(state: SystemState, key: str, default: Any = None) -> Any:
    """Safely reads a runtime system configuration parameter without side effects."""
    return state.configuration.get(key, default)
