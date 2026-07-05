"""Strict validation rules ensuring structural portfolio safety."""

from alphalab.common.validators import (
    require_mapping_key,
    require_missing_mapping_key,
    require_non_empty_string,
)
from alphalab.portfolio_optimizer.exceptions import PortfolioValidationError
from alphalab.portfolio_optimizer.state import PortfolioEngineState
from alphalab.portfolio_optimizer.targets import Portfolio


def validate_portfolio_creation(state: PortfolioEngineState, portfolio: Portfolio) -> None:
    require_non_empty_string(
        portfolio.portfolio_id,
        "portfolio_id",
        message="Portfolio ID cannot be empty.",
        exception_type=PortfolioValidationError,
    )
    require_missing_mapping_key(
        state.portfolios,
        portfolio.portfolio_id,
        f"Portfolio {portfolio.portfolio_id} already exists.",
        exception_type=PortfolioValidationError,
    )


def validate_portfolio_exists(state: PortfolioEngineState, portfolio_id: str) -> None:
    require_mapping_key(
        state.portfolios,
        portfolio_id,
        f"Portfolio {portfolio_id} not found.",
        exception_type=PortfolioValidationError,
    )
