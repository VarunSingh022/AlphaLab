"""Stateless registry operations establishing portfolios and base allocations."""

import uuid
from dataclasses import replace

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
        return str(uuid.uuid4())

    @staticmethod
    def create(
        state: PortfolioEngineState, portfolio: Portfolio, ts: float
    ) -> PortfolioEngineState:
        validate_portfolio_creation(state, portfolio)

        new_ports = dict(state.portfolios)
        new_ports[portfolio.portfolio_id] = portfolio

        evt = PortfolioCreated(PortfolioRegistry._create_id(), ts, portfolio.portfolio_id)
        return replace(state, portfolios=new_ports, events=(*state.events, evt))

    @staticmethod
    def allocate(
        state: PortfolioEngineState, alloc: CapitalAllocation, ts: float
    ) -> PortfolioEngineState:
        validate_portfolio_exists(state, alloc.portfolio_id)

        new_allocs = dict(state.allocations)
        new_allocs[alloc.portfolio_id] = alloc

        evt = AllocationChanged(
            PortfolioRegistry._create_id(),
            ts,
            alloc.portfolio_id,
            alloc.total_capital,
        )
        return replace(state, allocations=new_allocs, events=(*state.events, evt))
