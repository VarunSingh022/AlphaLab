"""Stateless registry operations establishing portfolios and base allocations."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.common.registry import with_mapping_item
from alphalab.portfolio_optimizer.allocation import CapitalAllocation
from alphalab.portfolio_optimizer.events import AllocationChanged, PortfolioCreated
from alphalab.portfolio_optimizer.state import PortfolioEngineState
from alphalab.portfolio_optimizer.targets import Portfolio
from alphalab.portfolio_optimizer.validation import (
    validate_portfolio_creation,
    validate_portfolio_exists,
)


class PortfolioRegistry:
    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def create(
        state: PortfolioEngineState, portfolio: Portfolio, ts: float
    ) -> PortfolioEngineState:
        validate_portfolio_creation(state, portfolio)

        new_ports = with_mapping_item(state.portfolios, portfolio.portfolio_id, portfolio)

        evt = PortfolioCreated(PortfolioRegistry._create_id(), ts, portfolio.portfolio_id)
        return replace(state, portfolios=new_ports, events=(*state.events, evt))

    @staticmethod
    def allocate(
        state: PortfolioEngineState, alloc: CapitalAllocation, ts: float
    ) -> PortfolioEngineState:
        validate_portfolio_exists(state, alloc.portfolio_id)

        new_allocs = with_mapping_item(state.allocations, alloc.portfolio_id, alloc)

        evt = AllocationChanged(
            PortfolioRegistry._create_id(),
            ts,
            alloc.portfolio_id,
            alloc.total_capital,
        )
        return replace(state, allocations=new_allocs, events=(*state.events, evt))
