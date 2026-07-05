"""
AlphaLab Examples
=================

Example 08 : Strategy Studio

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 07

Topics
------

• Strategy Studio
• Project Management
• Strategy Registration
• Backtesting
• Research Pipelines
• Report Generation
• Sessions
• Workspace Management

Run

    python examples/08_strategy_studio.py
"""

from alphalab.studio import (
    BacktestConfiguration,
    PipelineStep,
    Project,
    StrategyDefinition,
    StrategyStudioEngine,
    build_pipeline,
    pipeline_summary,
    project_summary,
    report_summary,
    studio_metrics,
    workspace_summary,
)


def main() -> None:
    """Demonstrate the complete Strategy Studio workflow."""

    # ------------------------------------------------------------
    # Step 1 : Initialize Studio
    # ------------------------------------------------------------

    state = StrategyStudioEngine.initialize(
        engine_id="STUDIO-001",
        workspace_dir="./workspace",
    )

    # ------------------------------------------------------------
    # Step 2 : Create Project
    # ------------------------------------------------------------

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

    # ------------------------------------------------------------
    # Step 3 : Register Strategy
    # ------------------------------------------------------------

    strategy = StrategyDefinition(
        strategy_id="MEAN_REV_V1",
        name="Mean Reversion",
        version="1.0.0",
        author="AlphaLab",
        description="Example institutional strategy.",
        parameters={
            "lookback": 20.0,
            "entry_zscore": 2.0,
            "exit_zscore": 0.5,
        },
    )

    state = StrategyStudioEngine.register_strategy(
        state,
        project.project_id,
        strategy,
        ts=1_720_000_001.0,
    )

    # ------------------------------------------------------------
    # Step 4 : Configure Backtest
    # ------------------------------------------------------------

    config = BacktestConfiguration(
        backtest_id="BT-001",
        strategy_id=strategy.strategy_id,
        dataset_ids=("sample_ohlcv",),
        start_timestamp=1_700_000_000.0,
        end_timestamp=1_710_000_000.0,
        initial_capital=1_000_000.0,
    )

    state = StrategyStudioEngine.run_backtest(
        state,
        project.project_id,
        config,
        simulated_metrics={
            "total_return": 0.186,
            "sharpe_ratio": 1.82,
            "max_drawdown": 0.072,
        },
        ts=1_720_000_002.0,
    )

    # ------------------------------------------------------------
    # Step 5 : Build Research Pipeline
    # ------------------------------------------------------------

    pipeline = build_pipeline(
        pipeline_id="PIPELINE-001",
        project_id=project.project_id,
        name="Complete Research Pipeline",
        steps=(
            PipelineStep.LOAD_DATA,
            PipelineStep.CLEAN_DATA,
            PipelineStep.NORMALIZE,
            PipelineStep.RESEARCH,
            PipelineStep.GENERATE_SIGNALS,
            PipelineStep.OPTIMIZE_PORTFOLIO,
            PipelineStep.REPLAY,
            PipelineStep.REPORT,
        ),
    )

    state = StrategyStudioEngine.run_pipeline(
        state,
        project.project_id,
        pipeline,
        simulated_metrics={
            "research_score": 91.2,
            "robustness": 88.5,
            "capacity": 79.4,
        },
        duration=3.72,
        ts=1_720_000_003.0,
    )

    # ------------------------------------------------------------
    # Step 6 : Generate Report
    # ------------------------------------------------------------

    state = StrategyStudioEngine.generate_report(
        state,
        project.project_id,
        report_type="Performance Summary",
        content="Example Strategy Studio report.",
        metrics=(0.186, 1.82, 0.072),
        ts=1_720_000_004.0,
    )

    # ------------------------------------------------------------
    # Step 7 : Start Session
    # ------------------------------------------------------------

    state = StrategyStudioEngine.start_session(
        state,
        session_id="SESSION-001",
        user_id="demo_user",
        project_id=project.project_id,
        ts=1_720_000_005.0,
    )

    # ------------------------------------------------------------
    # Step 8 : Save Workspace
    # ------------------------------------------------------------

    state = StrategyStudioEngine.save_workspace(
        state,
        workspace_id="WORKSPACE-001",
        ts=1_720_000_006.0,
    )

    # ------------------------------------------------------------
    # Step 9 : Inspect Results
    # ------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Example 08")
    print("Strategy Studio")
    print("=" * 60)
    print()

    print(f"Projects      : {len(project_summary(state))}")
    print(f"Pipelines     : {len(pipeline_summary(state))}")
    print(f"Reports       : {len(report_summary(state))}")
    print(f"Workspaces    : {len(workspace_summary(state))}")

    metrics = studio_metrics(state)

    print()
    print("Studio Metrics")
    print(metrics)

    print()
    print(f"Studio Events : {len(state.events)}")


if __name__ == "__main__":
    main()
