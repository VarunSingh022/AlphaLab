"""Strict validation rules ensuring structural portfolio safety."""

from alphalab.portfolio_optimizer.exceptions import PortfolioValidationError
from alphalab.portfolio_optimizer.state import PortfolioEngineState
from alphalab.portfolio_optimizer.targets import Portfolio


def validate_portfolio_creation(state: PortfolioEngineState, portfolio: Portfolio) -> None:
    if not portfolio.portfolio_id.strip():
        raise PortfolioValidationError("Portfolio ID cannot be empty.")
    if portfolio.portfolio_id in state.portfolios:
        raise PortfolioValidationError(f"Portfolio {portfolio.portfolio_id} already exists.")


def validate_portfolio_exists(state: PortfolioEngineState, portfolio_id: str) -> None:
    if portfolio_id not in state.portfolios:
        raise PortfolioValidationError(f"Portfolio {portfolio_id} not found.")
