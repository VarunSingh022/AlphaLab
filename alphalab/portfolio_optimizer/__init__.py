"""AlphaLab Portfolio Construction & Optimization Engine."""

from alphalab.portfolio_optimizer.adapter import PortfolioAdapter
from alphalab.portfolio_optimizer.allocation import CapitalAllocation
from alphalab.portfolio_optimizer.constraints import (
    RiskConstraints,
    WeightConstraints,
    apply_weight_constraints,
)
from alphalab.portfolio_optimizer.costs import CostModel, TransactionCostEstimate
from alphalab.portfolio_optimizer.engine import PortfolioEngine
from alphalab.portfolio_optimizer.events import (
    AllocationChanged,
    ConstraintViolated,
    ExposureUpdated,
    PortfolioCreated,
    PortfolioEvent,
    PortfolioUpdated,
    Rebalanced,
    WeightsCalculated,
)
from alphalab.portfolio_optimizer.exceptions import (
    ConstraintViolationError,
    InvalidPortfolioStateError,
    OptimizationError,
    PortfolioEngineError,
    PortfolioValidationError,
)
from alphalab.portfolio_optimizer.exposure import PortfolioExposure
from alphalab.portfolio_optimizer.metrics import (
    PortfolioMetrics,
    calculate_max_drawdown,
    calculate_volatility,
)
from alphalab.portfolio_optimizer.optimizer import (
    optimize_equal_weight,
    optimize_inverse_volatility,
    optimize_maximum_sharpe,
    optimize_minimum_variance,
)
from alphalab.portfolio_optimizer.protocol import AlphaSignalProtocol, RiskModelProtocol
from alphalab.portfolio_optimizer.rebalance import (
    RebalanceTrigger,
    check_schedule_rebalance,
    check_threshold_rebalance,
)
from alphalab.portfolio_optimizer.risk import validate_risk_constraints
from alphalab.portfolio_optimizer.state import PortfolioEngineState
from alphalab.portfolio_optimizer.targets import Portfolio
from alphalab.portfolio_optimizer.transactions import TargetTransaction
from alphalab.portfolio_optimizer.validation import (
    validate_portfolio_creation,
    validate_portfolio_exists,
)
from alphalab.portfolio_optimizer.views import (
    allocation_report,
    expected_costs,
    exposure_report,
    portfolio_metrics,
    portfolio_summary,
    weight_breakdown,
)
from alphalab.portfolio_optimizer.weights import TargetWeights

__all__ = [
    "AllocationChanged",
    "AlphaSignalProtocol",
    "CapitalAllocation",
    "ConstraintViolated",
    "ConstraintViolationError",
    "CostModel",
    "ExposureUpdated",
    "InvalidPortfolioStateError",
    "OptimizationError",
    "Portfolio",
    "PortfolioAdapter",
    "PortfolioCreated",
    "PortfolioEngine",
    "PortfolioEngineError",
    "PortfolioEngineState",
    "PortfolioEvent",
    "PortfolioExposure",
    "PortfolioMetrics",
    "PortfolioUpdated",
    "PortfolioValidationError",
    "RebalanceTrigger",
    "Rebalanced",
    "RiskConstraints",
    "RiskModelProtocol",
    "TargetTransaction",
    "TargetWeights",
    "TransactionCostEstimate",
    "WeightConstraints",
    "WeightsCalculated",
    "allocation_report",
    "apply_weight_constraints",
    "calculate_max_drawdown",
    "calculate_volatility",
    "check_schedule_rebalance",
    "check_threshold_rebalance",
    "expected_costs",
    "exposure_report",
    "optimize_equal_weight",
    "optimize_inverse_volatility",
    "optimize_maximum_sharpe",
    "optimize_minimum_variance",
    "portfolio_metrics",
    "portfolio_summary",
    "validate_portfolio_creation",
    "validate_portfolio_exists",
    "validate_risk_constraints",
    "weight_breakdown",
]
