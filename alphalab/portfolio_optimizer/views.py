"""Pure queries exposing transparent Portfolio State access."""

from collections.abc import Sequence

from alphalab.portfolio_optimizer.allocation import CapitalAllocation
from alphalab.portfolio_optimizer.costs import TransactionCostEstimate
from alphalab.portfolio_optimizer.exposure import PortfolioExposure
from alphalab.portfolio_optimizer.metrics import PortfolioMetrics
from alphalab.portfolio_optimizer.state import PortfolioEngineState
from alphalab.portfolio_optimizer.targets import Portfolio
from alphalab.portfolio_optimizer.weights import TargetWeights


def portfolio_summary(state: PortfolioEngineState) -> Sequence[Portfolio]:
    return tuple(state.portfolios.values())


def weight_breakdown(state: PortfolioEngineState, port_id: str) -> TargetWeights | None:
    return state.weights.get(port_id)


def allocation_report(state: PortfolioEngineState, port_id: str) -> CapitalAllocation | None:
    return state.allocations.get(port_id)


def exposure_report(state: PortfolioEngineState, port_id: str) -> PortfolioExposure | None:
    return state.exposures.get(port_id)


def portfolio_metrics(state: PortfolioEngineState, port_id: str) -> PortfolioMetrics | None:
    return state.metrics.get(port_id)


def expected_costs(state: PortfolioEngineState, port_id: str) -> TransactionCostEstimate | None:
    return state.cost_estimates.get(port_id)
