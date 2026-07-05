"""
Example 01 - Research Engine

This example demonstrates the public AlphaLab Research API.

Topics
------
- Initializing a research session
- Creating a ResearchPayload
- Running the complete research pipeline
- Inspecting the resulting immutable ResearchState

Run

    python examples/01_research.py
"""

from alphalab.research import (
    ResearchEngine,
    ResearchPayload,
    TradePayload,
    overall_score,
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
    )

    # ------------------------------------------------------------------
    # Step 3: Execute the research pipeline
    # ------------------------------------------------------------------

    result = ResearchEngine.run_full_research(
        state=state,
        payload=payload,
        timestamp=1_720_000_001.0,
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

    score = overall_score(result)

    if score is not None:
        print(f"Overall Score : {score.overall_score:.2f}")

    print()

    print("Warnings")

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
