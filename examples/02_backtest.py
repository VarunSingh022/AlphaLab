"""
AlphaLab Examples
=================

Example 02 : Strategy Studio Backtest

Difficulty : Beginner

Estimated Time : 5 minutes

Topics
------

• Strategy Studio
• Project Management
• Strategy Registration
• Backtesting
• Immutable State
• Studio Views

Run

    python examples/02_backtest.py
"""

from alphalab.studio import (
    BacktestConfiguration,
    Project,
    StrategyDefinition,
    StrategyStudioEngine,
    backtest_summary,
    project_summary,
)


def main() -> None:
    """Run a simple deterministic backtest using Strategy Studio."""

    # ------------------------------------------------------------------
    # Step 1 : Initialize Studio
    # ------------------------------------------------------------------

    state = StrategyStudioEngine.initialize(
        engine_id="STUDIO-001",
        workspace_dir="./workspace",
    )

    # ------------------------------------------------------------------
    # Step 2 : Create Project
    # ------------------------------------------------------------------

    project = Project(
        project_id="PROJECT-001",
        name="Mean Reversion Research",
        created_at=1_720_000_000.0,
    )

    state = StrategyStudioEngine.create_project(
        state,
        project,
        ts=1_720_000_000.0,
    )

    # ------------------------------------------------------------------
    # Step 3 : Register Strategy
    # ------------------------------------------------------------------

    strategy = StrategyDefinition(
        strategy_id="MEAN_REV_V1",
        name="Mean Reversion",
        version="1.0.0",
        author="AlphaLab",
        description="Simple mean reversion demonstration strategy.",
        parameters={
            "lookback": 20.0,
            "z_score": 2.0,
        },
    )

    state = StrategyStudioEngine.register_strategy(
        state,
        project.project_id,
        strategy,
        ts=1_720_000_001.0,
    )

    # ------------------------------------------------------------------
    # Step 4 : Configure Backtest
    # ------------------------------------------------------------------

    config = BacktestConfiguration(
        backtest_id="BT-001",
        strategy_id=strategy.strategy_id,
        dataset_ids=("sample_prices",),
        start_timestamp=1_700_000_000.0,
        end_timestamp=1_710_000_000.0,
        initial_capital=1_000_000.0,
    )

    # ------------------------------------------------------------------
    # Step 5 : Execute Backtest
    # ------------------------------------------------------------------

    simulated_metrics = {
        "total_return": 0.184,
        "sharpe_ratio": 1.82,
        "max_drawdown": 0.071,
    }

    state = StrategyStudioEngine.run_backtest(
        state,
        project.project_id,
        config,
        simulated_metrics,
        ts=1_720_000_002.0,
    )

    # ------------------------------------------------------------------
    # Step 6 : Inspect Results
    # ------------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Strategy Studio Example")
    print("=" * 60)

    print()

    print(f"Projects : {len(project_summary(state))}")

    print(f"Backtests : {len(backtest_summary(state))}")

    print()

    for result in backtest_summary(state):
        print(f"Result ID      : {result.result_id}")
        print(f"Backtest ID    : {result.backtest_id}")
        print(f"Strategy       : {result.strategy_id}")
        print(f"Total Return   : {result.total_return:.2%}")
        print(f"Sharpe Ratio   : {result.sharpe_ratio:.2f}")
        print(f"Max Drawdown   : {result.max_drawdown:.2%}")
        print()

    print(f"Studio Events : {len(state.events)}")


if __name__ == "__main__":
    main()
