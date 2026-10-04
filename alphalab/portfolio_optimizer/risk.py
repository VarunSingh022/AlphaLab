"""Evaluators verifying adherence to risk parameters."""

from alphalab.portfolio_optimizer.constraints import RiskConstraints
from alphalab.portfolio_optimizer.exceptions import ConstraintViolationError
from alphalab.portfolio_optimizer.metrics import PortfolioMetrics


def validate_risk_constraints(metrics: PortfolioMetrics, constraints: RiskConstraints) -> None:
    """Throws an error if the computed metrics breach risk limits.

    Checks every limit :class:`RiskConstraints` states -- drawdown, volatility
    and turnover -- against what :class:`PortfolioMetrics` measures. Until
    v3.13 the constraints also carried a tracking error, a leverage and a
    concentration limit that nothing here could check; they are removed rather
    than left looking like limits (see :class:`RiskConstraints`). Leverage and
    concentration are enforced *inside* a v3.8 construction by
    ``ConstraintSet.max_gross_exposure`` and ``ConstraintSet.max_abs_weight``.
    """
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
