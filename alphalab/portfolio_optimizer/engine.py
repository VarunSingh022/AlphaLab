"""Top-level Engine Facade orchestrating Portfolio Construction."""

from typing import Any

from alphalab.portfolio_optimizer.allocation import CapitalAllocation
from alphalab.portfolio_optimizer.constraints import WeightConstraints
from alphalab.portfolio_optimizer.costs import CostModel
from alphalab.portfolio_optimizer.manager import PortfolioManager
from alphalab.portfolio_optimizer.rebalance import RebalanceTrigger
from alphalab.portfolio_optimizer.registry import PortfolioRegistry
from alphalab.portfolio_optimizer.state import PortfolioEngineState
from alphalab.portfolio_optimizer.targets import Portfolio


class PortfolioEngine:
    """Facade for managing deterministic portfolio assembly and tracking."""

    @staticmethod
    def initialize(engine_id: str) -> PortfolioEngineState:
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        return PortfolioEngineState(engine_id=engine_id)

    @staticmethod
    def create(
        state: PortfolioEngineState, portfolio: Portfolio, ts: float
    ) -> PortfolioEngineState:
        return PortfolioRegistry.create(state, portfolio, ts)

    @staticmethod
    def allocate(
        state: PortfolioEngineState, alloc: CapitalAllocation, ts: float
    ) -> PortfolioEngineState:
        return PortfolioRegistry.allocate(state, alloc, ts)

    @staticmethod
    def optimize(
        state: PortfolioEngineState,
        port_id: str,
        method: str,
        symbols: tuple[str, ...],
        params: dict[str, Any],
        ts: float,
    ) -> PortfolioEngineState:
        return PortfolioManager.optimize(state, port_id, method, symbols, params, ts)

    @staticmethod
    def apply_constraints(
        state: PortfolioEngineState, port_id: str, constraints: WeightConstraints, ts: float
    ) -> PortfolioEngineState:
        return PortfolioManager.apply_constraints(state, port_id, constraints, ts)

    @staticmethod
    def rebalance(
        state: PortfolioEngineState,
        port_id: str,
        current_w: dict[str, float],
        trigger: RebalanceTrigger,
        last_ts: float,
        current_ts: float,
    ) -> PortfolioEngineState:
        return PortfolioManager.rebalance(state, port_id, current_w, trigger, last_ts, current_ts)

    @staticmethod
    def estimate_costs(
        state: PortfolioEngineState,
        port_id: str,
        current_w: dict[str, float],
        model: CostModel,
        capital: float,
        ts: float,
    ) -> PortfolioEngineState:
        return PortfolioManager.estimate_costs(state, port_id, current_w, model, capital, ts)
