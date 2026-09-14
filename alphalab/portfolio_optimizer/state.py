"""Global immutable state container for the Portfolio Engine.

The eight keyed indexes and the event log use the canonical containers from
:mod:`alphalab.common`, for the reason v2.1 and v2.2 introduced them. Until
v2.17 every mutator rebuilt a whole ``dict`` and a whole ``tuple`` per
transition, so ``N`` transitions copied ``O(N^2)`` entries -- ADR-0032 category C
finding 1, closed by ADR-0034. Both containers are immutable, both define value
equality, and a ``PersistentMap`` iterates in first-insertion order of the keys
still present, which is what a ``dict`` does.

This is the ``PortfolioEngine`` that answers *what should I own*. The accounting
one -- *what do I own and what is it worth* -- is
:class:`alphalab.portfolio.engine.PortfolioEngine`, and they share no operation
(ADR-0032 finding B2).
"""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
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
    portfolios: PersistentMap[str, Portfolio] = field(default_factory=PersistentMap)
    weights: PersistentMap[str, TargetWeights] = field(default_factory=PersistentMap)
    constraints: PersistentMap[str, WeightConstraints] = field(default_factory=PersistentMap)
    risk_limits: PersistentMap[str, RiskConstraints] = field(default_factory=PersistentMap)
    metrics: PersistentMap[str, PortfolioMetrics] = field(default_factory=PersistentMap)
    exposures: PersistentMap[str, PortfolioExposure] = field(default_factory=PersistentMap)
    allocations: PersistentMap[str, CapitalAllocation] = field(default_factory=PersistentMap)
    cost_estimates: PersistentMap[str, TransactionCostEstimate] = field(
        default_factory=PersistentMap
    )
    events: AppendOnlyLog[PortfolioEvent] = field(default_factory=AppendOnlyLog)
