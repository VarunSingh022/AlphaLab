"""Pure deterministic query functions (selectors) for navigating immutable state hierarchies."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from alphalab.kernel.state import SystemState
from alphalab.portfolio.nav import NAVCalculator
from alphalab.portfolio.pnl import PnLEngine
from alphalab.portfolio.position import Position


def get_cash(state: SystemState) -> Decimal:
    """Returns the current cash balance."""
    return state.portfolio.cash.balance(state.portfolio.account.base_currency)


def get_positions(state: SystemState) -> Mapping[str, Position]:
    """Returns an immutable mapping of all portfolio positions."""
    return state.portfolio.positions


def get_equity(state: SystemState) -> Decimal:
    """Returns total portfolio equity (cash + open positions market value)."""
    return NAVCalculator.calculate(
        cash_ledger=state.portfolio.cash,
        positions=state.portfolio.positions,
        base_currency=state.portfolio.account.base_currency,
    )


def get_symbol_position(state: SystemState, symbol: str) -> Position | None:
    """Retrieves the exact position state for a given ticker symbol, or None if unassigned."""
    return state.portfolio.positions.get(symbol)


def get_portfolio_value(state: SystemState) -> Decimal:
    """Returns total portfolio equity (alias for standardized accounting conventions)."""
    return get_equity(state)


def get_realized_pnl(state: SystemState) -> Decimal:
    """Returns aggregate realized profit and loss."""
    return PnLEngine.realized_pnl(state.portfolio.positions)


def get_unrealized_pnl(state: SystemState) -> Decimal:
    """Returns aggregate open profit and loss across all positions."""
    return PnLEngine.unrealized_pnl(state.portfolio.positions)


def get_market_price(state: SystemState, symbol: str) -> float | None:
    """Returns the most recent price observation for a specific instrument."""
    return state.market.prices.get(symbol)


def get_configuration_parameter(state: SystemState, key: str, default: Any = None) -> Any:
    """Safely reads a runtime system configuration parameter without side effects."""
    return state.configuration.get(key, default)
