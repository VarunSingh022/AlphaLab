"""
AlphaLab Examples
=================

Example 06 : Portfolio Optimizer

Difficulty : Intermediate

Estimated Time : 7 minutes

Prerequisites
-------------

✓ Example 05

Topics
------

• Portfolio Creation
• Capital Allocation
• Weight Optimization
• Portfolio Constraints
• Transaction Cost Estimation
• Portfolio Views

Run

    python examples/06_portfolio_optimizer.py
"""

from alphalab.portfolio_optimizer import (
    CapitalAllocation,
    CostModel,
    Portfolio,
    PortfolioEngine,
    WeightConstraints,
    allocation_report,
    expected_costs,
    portfolio_summary,
    weight_breakdown,
)


def main() -> None:
    """Demonstrate the AlphaLab Portfolio Optimizer."""

    # ------------------------------------------------------------
    # Step 1 : Initialize Engine
    # ------------------------------------------------------------

    state = PortfolioEngine.initialize(
        engine_id="PORTFOLIO-001",
    )

    # ------------------------------------------------------------
    # Step 2 : Create Portfolio
    # ------------------------------------------------------------

    portfolio = Portfolio(
        portfolio_id="PORT-001",
        name="Mean Reversion Portfolio",
        base_currency="USD",
        created_at=1_720_000_000.0,
    )

    state = PortfolioEngine.create(
        state,
        portfolio,
        ts=1_720_000_000.0,
    )

    # ------------------------------------------------------------
    # Step 3 : Allocate Capital
    # ------------------------------------------------------------

    allocation = CapitalAllocation(
        portfolio_id=portfolio.portfolio_id,
        total_capital=1_000_000.0,
        invested_capital=950_000.0,
        cash_balance=50_000.0,
        margin_used=0.0,
        leverage_ratio=1.0,
    )

    state = PortfolioEngine.allocate(
        state,
        allocation,
        ts=1_720_000_001.0,
    )

    # ------------------------------------------------------------
    # Step 4 : Optimize Portfolio
    # ------------------------------------------------------------

    symbols = (
        "AAPL",
        "MSFT",
        "SPY",
    )

    state = PortfolioEngine.optimize(
        state,
        portfolio.portfolio_id,
        method="EQUAL_WEIGHT",
        symbols=symbols,
        params={},
        ts=1_720_000_002.0,
    )

    # ------------------------------------------------------------
    # Step 5 : Apply Constraints
    # ------------------------------------------------------------

    constraints = WeightConstraints(
        long_only=True,
        max_position_weight=0.40,
        cash_reserve_weight=0.05,
    )

    state = PortfolioEngine.apply_constraints(
        state,
        portfolio.portfolio_id,
        constraints,
        ts=1_720_000_003.0,
    )

    # ------------------------------------------------------------
    # Step 6 : Estimate Transaction Costs
    # ------------------------------------------------------------

    current_weights = {
        "AAPL": 0.30,
        "MSFT": 0.40,
        "SPY": 0.30,
    }

    cost_model = CostModel(
        commission_rate=0.001,
        slippage_rate=0.001,
        spread_rate=0.0002,
        market_impact_rate=0.0005,
        fixed_exchange_fee=1.0,
    )

    state = PortfolioEngine.estimate_costs(
        state,
        portfolio.portfolio_id,
        current_weights,
        cost_model,
        capital=1_000_000.0,
        ts=1_720_000_004.0,
    )

    # ------------------------------------------------------------
    # Step 7 : Inspect Results
    # ------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Example 06")
    print("Portfolio Optimizer")
    print("=" * 60)
    print()

    print(f"Portfolios : {len(portfolio_summary(state))}")

    alloc = allocation_report(
        state,
        portfolio.portfolio_id,
    )

    if alloc is not None:
        print(f"Capital    : ${alloc.total_capital:,.2f}")

    weights = weight_breakdown(
        state,
        portfolio.portfolio_id,
    )

    if weights is not None:
        print()
        print("Target Weights")

        for symbol, weight in weights.weights.items():
            print(f"  {symbol:<6} {weight:.2%}")

    costs = expected_costs(
        state,
        portfolio.portfolio_id,
    )

    if costs is not None:
        print()
        print(f"Estimated Trading Cost : ${costs.total_estimated_cost:,.2f}")

    print()
    print(f"Events Generated : {len(state.events)}")


if __name__ == "__main__":
    main()
