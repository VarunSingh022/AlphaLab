"""Global immutable state container for the Portfolio Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.portfolio_optimizer.allocation import CapitalAllocation
from alphalab.portfolio_optimizer.constraints import RiskConstraints, WeightConstraints
from alphalab.portfolio_optimizer.costs import TransactionCostEstimate
from alphalab.portfolio_optimizer.events import PortfolioEvent
from alphalab.portfolio_optimizer.exposure import PortfolioExposure
from alphalab.portfolio_optimizer.metrics import PortfolioMetrics
from alphalab.portfolio_optimizer.targets import Portfolio
from alphalab.portfolio_optimizer.weights import TargetWeights


@dataclass(frozen=True, slots=True)
class PortfolioEngineState:
    """Deterministic snapshot of the Portfolio Management cluster."""

    engine_id: str
    portfolios: Mapping[str, Portfolio] = field(default_factory=dict)
    weights: Mapping[str, TargetWeights] = field(default_factory=dict)
    constraints: Mapping[str, WeightConstraints] = field(default_factory=dict)
    risk_limits: Mapping[str, RiskConstraints] = field(default_factory=dict)
    metrics: Mapping[str, PortfolioMetrics] = field(default_factory=dict)
    exposures: Mapping[str, PortfolioExposure] = field(default_factory=dict)
    allocations: Mapping[str, CapitalAllocation] = field(default_factory=dict)
    cost_estimates: Mapping[str, TransactionCostEstimate] = field(default_factory=dict)
    events: tuple[PortfolioEvent, ...] = field(default_factory=tuple)
