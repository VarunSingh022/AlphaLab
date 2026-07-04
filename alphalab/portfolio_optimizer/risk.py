"""Evaluators verifying adherence to risk parameters."""

from alphalab.portfolio_optimizer.constraints import RiskConstraints
from alphalab.portfolio_optimizer.exceptions import ConstraintViolationError
from alphalab.portfolio_optimizer.metrics import PortfolioMetrics


def validate_risk_constraints(metrics: PortfolioMetrics, constraints: RiskConstraints) -> None:
    """Throws an error if the computed metrics breach risk limits."""
    if metrics.max_drawdown > constraints.max_drawdown_limit:
        raise ConstraintViolationError(
            f"Drawdown {metrics.max_drawdown} exceeds limit {constraints.max_drawdown_limit}"
        )
    if metrics.volatility > constraints.max_volatility_limit:
        raise ConstraintViolationError(
            f"Volatility {metrics.volatility} exceeds limit {constraints.max_volatility_limit}"
        )
    if metrics.turnover > constraints.max_turnover:
        raise ConstraintViolationError(
            f"Turnover {metrics.turnover} exceeds limit {constraints.max_turnover}"
        )
