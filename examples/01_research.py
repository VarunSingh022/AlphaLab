"""
Example 01 - Research Engine

This example demonstrates the public AlphaLab Research API.

Topics
------
- Initializing a research session
- Creating a ResearchPayload, stating how many returns make a year and the
  risk-free rate (nothing assumes daily returns since v3.12)
- Stating the bounds the evaluation is judged against (a ResearchPolicy)
- Running the complete research pipeline
- Inspecting the resulting immutable ResearchState: its measurements and the
  findings -- there is no overall grade

Run

    python examples/01_research.py
"""

from alphalab.research import (
    ResearchEngine,
    ResearchPayload,
    ResearchPolicy,
    TradePayload,
    research_metrics_of,
    warnings,
)


def main() -> None:
    """Run a complete research workflow."""

    # ------------------------------------------------------------------
    # Step 1: Initialize a research session
    # ------------------------------------------------------------------

    state = ResearchEngine.initialize(
        research_id="RESEARCH-001",
        strategy_id="MEAN_REVERSION",
        timestamp=1_720_000_000.0,
    )

    # ------------------------------------------------------------------
    # Step 2: Create research payload
    # ------------------------------------------------------------------

    payload = ResearchPayload(
        strategy_id="MEAN_REVERSION",
        returns=(
            0.012,
            -0.004,
            0.008,
            0.015,
            -0.002,
            0.011,
        ),
        trades=(
            TradePayload(
                trade_id="T-001",
                symbol="AAPL",
                entry_price=180.50,
                exit_price=183.25,
                quantity=100,
                pnl=275.0,
                duration_seconds=3600,
            ),
            TradePayload(
                trade_id="T-002",
                symbol="MSFT",
                entry_price=420.0,
                exit_price=418.5,
                quantity=50,
                pnl=-75.0,
                duration_seconds=2400,
            ),
        ),
        parameters={
            "lookback": 20,
            "z_score": 2.0,
        },
        market_regimes=(
            "Bull",
            "Bull",
            "Bull",
            "Sideways",
            "Sideways",
            "Bull",
        ),
        aum=1_000_000.0,
        # Six daily returns: a year holds 252 of them, and excess returns are
        # measured over a 4% annual rate. Both are stated, never assumed.
        periods_per_year=252,
        risk_free_rate=0.04,
    )

    # ------------------------------------------------------------------
    # Step 2b: State the bounds the evaluation is judged against
    # ------------------------------------------------------------------

    policy = ResearchPolicy(
        walk_forward_windows=3,
        ruin_drawdown=0.20,
        minimum_trades=50,
        maximum_trade_share=0.30,
        worst_period_return=-0.10,
        shock_return=-0.10,
        gain_multiplier=0.5,
        loss_multiplier=2.0,
    )

    # ------------------------------------------------------------------
    # Step 3: Execute the research pipeline
    # ------------------------------------------------------------------

    result = ResearchEngine.run_full_research(
        state=state,
        payload=payload,
        policy=policy,
        timestamp=1_720_000_001.0,
        # The resampling seed is stated, not assumed: v3.10 removed the default.
        # 42 is the value that default was, so this reproduces the v3.9 figures.
        seed=42,
    )

    # ------------------------------------------------------------------
    # Step 4: Inspect results
    # ------------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Research Example")
    print("=" * 60)

    print(f"Research ID : {result.research_id}")
    print(f"Strategy    : {result.strategy_id}")
    print(f"Completed   : {result.completed}")
    print()

    print("Measurements")
    for name, value in research_metrics_of(result).items():
        print(f"  {name:<32} {value: .4f}")

    print()

    print("Findings (each against a bound the policy stated)")

    report = warnings(result)

    if report:
        for item in report:
            print(f"  • {item}")
    else:
        print("  None")

    print()

    print(f"Events Generated : {len(result.events)}")


if __name__ == "__main__":
    main()
