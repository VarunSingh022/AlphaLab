"""AlphaLab Portfolio Construction & Optimization Engine.

The construction authority (ADR-0005): *what should I own*. The v1 surface --
four closed-form optimizers, the post-hoc weight constraints and the engine
state -- is unchanged. v3.8 adds :func:`construct`, one entry point for
minimum variance, mean-variance, maximum diversification, risk parity and
robust mean-variance under explicit constraints, with certified statuses and
diagnostics, and :func:`black_litterman`, the model whose posterior feeds it
(ADR-0043).
"""

from alphalab.portfolio_optimizer.adapter import PortfolioAdapter
from alphalab.portfolio_optimizer.allocation import CapitalAllocation
from alphalab.portfolio_optimizer.black_litterman import (
    BLACK_LITTERMAN_SCHEME,
    BlackLittermanModel,
    BlackLittermanPosterior,
    EquilibriumPrior,
    InvestorView,
    SuppliedPrior,
    ViewDiagnostic,
    black_litterman,
    view_variance_from_prior,
)
from alphalab.portfolio_optimizer.constraints import (
    RiskConstraints,
    WeightConstraints,
    apply_weight_constraints,
)
from alphalab.portfolio_optimizer.construction import (
    CONSTRUCTION_PROBLEM_SCHEME,
    CONSTRUCTION_RESULT_SCHEME,
    EXPECTED_RETURNS_SCHEME,
    BoxUncertainty,
    ConstraintSet,
    ConstructionDiagnostics,
    ConstructionObjective,
    ConstructionProblem,
    ConstructionResult,
    ConstructionStatus,
    EllipsoidalUncertainty,
    ExpectedReturns,
    ExposureRange,
    FactorBound,
    GroupBound,
    MaximumDiversification,
    MeanVariance,
    MinimumVariance,
    NotionalLimits,
    RiskParity,
    RobustMeanVariance,
    SolverSettings,
    TurnoverLimit,
    WeightBounds,
    construct,
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
    ConstructionInputError,
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
    "BLACK_LITTERMAN_SCHEME",
    "CONSTRUCTION_PROBLEM_SCHEME",
    "CONSTRUCTION_RESULT_SCHEME",
    "EXPECTED_RETURNS_SCHEME",
    "AllocationChanged",
    "AlphaSignalProtocol",
    "BlackLittermanModel",
    "BlackLittermanPosterior",
    "BoxUncertainty",
    "CapitalAllocation",
    "ConstraintSet",
    "ConstraintViolated",
    "ConstraintViolationError",
    "ConstructionDiagnostics",
    "ConstructionInputError",
    "ConstructionObjective",
    "ConstructionProblem",
    "ConstructionResult",
    "ConstructionStatus",
    "CostModel",
    "EllipsoidalUncertainty",
    "EquilibriumPrior",
    "ExpectedReturns",
    "ExposureRange",
    "ExposureUpdated",
    "FactorBound",
    "GroupBound",
    "InvalidPortfolioStateError",
    "InvestorView",
    "MaximumDiversification",
    "MeanVariance",
    "MinimumVariance",
    "NotionalLimits",
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
    "RiskParity",
    "RobustMeanVariance",
    "SolverSettings",
    "SuppliedPrior",
    "TargetTransaction",
    "TargetWeights",
    "TransactionCostEstimate",
    "TurnoverLimit",
    "ViewDiagnostic",
    "WeightBounds",
    "WeightConstraints",
    "WeightsCalculated",
    "allocation_report",
    "apply_weight_constraints",
    "black_litterman",
    "calculate_max_drawdown",
    "calculate_volatility",
    "check_schedule_rebalance",
    "check_threshold_rebalance",
    "construct",
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
    "view_variance_from_prior",
    "weight_breakdown",
]
