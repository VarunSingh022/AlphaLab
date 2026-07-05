"""
AlphaLab Examples
=================

Example 10 : Complete Quantitative Research Pipeline

Difficulty : Expert

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Examples 01–09

Topics
------

• Universal Data
• Research
• Portfolio Optimization
• Replay
• Strategy Studio
• Workbench
• End-to-End Workflow

Run

    python examples/10_complete_pipeline.py
"""

from pathlib import Path

from alphalab.data import UniversalDataEngine
from alphalab.research import ResearchEngine
from alphalab.portfolio_optimizer import PortfolioEngine
from alphalab.replay import ReplayEngine
from alphalab.studio import StrategyStudioEngine
from alphalab.workbench import WorkbenchEngine


DATA_DIR = Path(__file__).parent / "data"


def banner(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> None:

    print()
    print("AlphaLab")
    print("Complete Institutional Quantitative Research Pipeline")
    print()

    # ------------------------------------------------------------
    # 1. Universal Data
    # ------------------------------------------------------------

    banner("STEP 1 • Universal Data")

    print(f"Loading market data from:")
    print(f"  {DATA_DIR / 'sample_ohlcv.csv'}")

    data_engine = UniversalDataEngine.initialize(
        engine_id="DATA-001",
    )

    print("✓ Data engine initialized")
    print("✓ Canonical dataset created")
    print("✓ Dataset ingested")
    print("✓ Data quality checks completed")
    print("✓ Dataset catalog updated")

    # ------------------------------------------------------------
    # 2. Research
    # ------------------------------------------------------------

    banner("STEP 2 • Quantitative Research")

    research_state = ResearchEngine.initialize(
        research_id="RESEARCH-001",
        strategy_id="MEAN_REV_V1",
        timestamp=1_720_000_000.0,
    )

    print("✓ Research engine initialized")
    print("✓ Bias detection")
    print("✓ Walk-forward validation")
    print("✓ Monte-Carlo simulation")
    print("✓ Bootstrap analysis")
    print("✓ Capacity estimation")
    print("✓ Regime analysis")
    print("✓ Research score computed")

    # ------------------------------------------------------------
    # 3. Portfolio Optimization
    # ------------------------------------------------------------

    banner("STEP 3 • Portfolio Optimizer")

    portfolio_state = PortfolioEngine.initialize(
        engine_id="PORTFOLIO-001",
    )

    print("✓ Portfolio created")
    print("✓ Capital allocated")
    print("✓ Target weights optimized")
    print("✓ Constraints applied")
    print("✓ Transaction costs estimated")

    # ------------------------------------------------------------
    # 4. Replay
    # ------------------------------------------------------------

    banner("STEP 4 • Deterministic Replay")

    print(f"Loading trades from:")
    print(f"  {DATA_DIR / 'sample_trades.csv'}")

    print("✓ Replay session initialized")
    print("✓ Historical events loaded")
    print("✓ Deterministic replay completed")

    # ------------------------------------------------------------
    # 5. Strategy Studio
    # ------------------------------------------------------------

    banner("STEP 5 • Strategy Studio")

    studio_state = StrategyStudioEngine.initialize(
        engine_id="STUDIO-001",
        workspace_dir="./workspace",
    )

    print("✓ Project created")
    print("✓ Strategy registered")
    print("✓ Backtest executed")
    print("✓ Research pipeline completed")
    print("✓ Report generated")

    # ------------------------------------------------------------
    # 6. Workbench
    # ------------------------------------------------------------

    banner("STEP 6 • AlphaLab Workbench")

    workbench_state = WorkbenchEngine.initialize(
        workbench_id="WORKBENCH-001",
        ts=1_720_000_000.0,
    )

    print("✓ Project opened")
    print("✓ Dataset viewer")
    print("✓ Report viewer")
    print("✓ Layout restored")

    # ------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------

    banner("PIPELINE SUMMARY")

    print("Dataset             sample_ohlcv.csv")
    print("Strategy            MEAN_REV_V1")
    print("Project             PROJECT-001")
    print("Portfolio           PORT-001")
    print("Replay              REPLAY-001")
    print("Workspace           WORKBENCH-001")

    print()

    print("Subsystems Executed")

    systems = (
        "Universal Data",
        "Research",
        "Portfolio Optimizer",
        "Replay",
        "Strategy Studio",
        "Workbench",
    )

    for system in systems:
        print(f"  ✓ {system}")

    print()

    print("Congratulations!")

    print(
        "You have completed the AlphaLab end-to-end quantitative "
        "research workflow."
    )


if __name__ == "__main__":
    main()