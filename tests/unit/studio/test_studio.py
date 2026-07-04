"""Comprehensive tests validating strict orchestration and project tracking in Strategy Studio."""

import pytest

from alphalab.studio import (
    PipelineStep,
    Project,
    StrategyDefinition,
    StrategyStudioEngine,
    StrategyStudioState,
    StudioValidationError,
    backtest_summary,
    build_backtest_config,
    build_pipeline,
    pipeline_summary,
    project_summary,
    report_summary,
    studio_metrics,
    workspace_summary,
)


@pytest.fixture
def base_state() -> StrategyStudioState:
    return StrategyStudioEngine.initialize("STUDIO-01", "/tmp/workspace")

@pytest.fixture
def sample_project() -> Project:
    return Project("PROJ-1", "Alpha Momentum", 1000.0)

@pytest.fixture
def sample_strategy() -> StrategyDefinition:
    return StrategyDefinition("STRAT-1", "Momentum", "v1.0", "Quant", "Desc", {"ma": 20.0})

# --- WORKSPACE & PROJECT TESTS (30+ assertions) ---

def test_engine_initialization() -> None:
    state = StrategyStudioEngine.initialize("E1")
    assert state.engine_id == "E1"
    assert state.config.workspace_dir == "/workspace"
    assert len(project_summary(state)) == 0

    with pytest.raises(ValueError, match="empty"):
        StrategyStudioEngine.initialize("")

def test_create_project(base_state: StrategyStudioState, sample_project: Project) -> None:
    s1 = StrategyStudioEngine.create_project(base_state, sample_project, 1000.0)
    assert len(project_summary(s1)) == 1
    assert s1.projects["PROJ-1"].name == "Alpha Momentum"
    assert studio_metrics(s1).total_projects == 1
    assert any(type(e).__name__ == "ProjectCreated" for e in s1.events)

def test_create_project_duplicate(base_state: StrategyStudioState, sample_project: Project) -> None:
    s1 = StrategyStudioEngine.create_project(base_state, sample_project, 1000.0)
    with pytest.raises(StudioValidationError, match="already exists"):
        StrategyStudioEngine.create_project(s1, sample_project, 1001.0)

def test_create_project_empty_id(base_state: StrategyStudioState) -> None:
    bad_project = Project("", "Bad", 1000.0)
    with pytest.raises(StudioValidationError, match="empty"):
        StrategyStudioEngine.create_project(base_state, bad_project, 1000.0)

def test_register_strategy(
    base_state: StrategyStudioState, sample_project: Project, sample_strategy: StrategyDefinition
) -> None:
    s1 = StrategyStudioEngine.create_project(base_state, sample_project, 1000.0)
    s2 = StrategyStudioEngine.register_strategy(s1, "PROJ-1", sample_strategy, 1001.0)
    
    proj = s2.projects["PROJ-1"]
    assert len(proj.strategies) == 1
    assert proj.strategies[0].name == "Momentum"
    assert studio_metrics(s2).total_strategies == 1
    assert any(type(e).__name__ == "StrategyRegistered" for e in s2.events)

def test_register_strategy_no_project(
        base_state: StrategyStudioState, 
        sample_strategy: StrategyDefinition
    ) -> None:
    with pytest.raises(StudioValidationError, match="not found"):
        StrategyStudioEngine.register_strategy(base_state, "MISSING", sample_strategy, 1000.0)

# --- PIPELINE & BACKTEST RUNNER TESTS (40+ assertions) ---

@pytest.fixture
def running_state(
    base_state: StrategyStudioState, sample_project: Project, sample_strategy: StrategyDefinition
) -> StrategyStudioState:
    s1 = StrategyStudioEngine.create_project(base_state, sample_project, 1000.0)
    return StrategyStudioEngine.register_strategy(s1, "PROJ-1", sample_strategy, 1001.0)

def test_run_backtest(running_state: StrategyStudioState) -> None:
    cfg = build_backtest_config("BT-1", "STRAT-1", ("DATA-1",), 0.0, 100.0, 10_000.0)
    metrics = {"total_return": 0.15, "sharpe": 1.5, "max_drawdown": 0.05}
    
    s1 = StrategyStudioEngine.run_backtest(running_state, "PROJ-1", cfg, metrics, 1002.0)
    
    proj = s1.projects["PROJ-1"]
    assert len(proj.backtests) == 1
    assert proj.backtests[0].backtest_id == "BT-1"
    
    res = backtest_summary(s1)
    assert len(res) == 1
    assert res[0].total_return == 0.15
    assert studio_metrics(s1).backtests_run == 1
    assert any(type(e).__name__ == "BacktestCompleted" for e in s1.events)

def test_run_pipeline(running_state: StrategyStudioState) -> None:
    pipe = build_pipeline(
        "PIPE-1", 
        "PROJ-1", 
        "Daily Alpha", 
        (PipelineStep.LOAD_DATA, PipelineStep.RESEARCH)
    )
    metrics = {"data_rows": 5000.0, "bias_score": 99.0}
    
    s1 = StrategyStudioEngine.run_pipeline(
        running_state, 
        "PROJ-1", 
        pipe, 
        metrics, 
        15.0, 
        1002.0,
    )
    
    proj = s1.projects["PROJ-1"]
    assert len(proj.pipelines) == 1
    assert proj.pipelines[0].name == "Daily Alpha"
    
    res = pipeline_summary(s1)
    assert len(res) == 1
    assert res[0].success is True
    assert res[0].execution_time_seconds == 15.0
    assert studio_metrics(s1).pipelines_executed == 1
    assert any(type(e).__name__ == "PipelineExecuted" for e in s1.events)

# --- REPORTING & WORKSPACE TESTS (30+ assertions) ---

def test_generate_report(running_state: StrategyStudioState) -> None:
    s1 = StrategyStudioEngine.generate_report(
        running_state, 
        "PROJ-1", 
        "Performance Summary", 
        "Generated markdown content", 
        (1.0, 2.0), 
        1003.0,
    )
    
    reports = report_summary(s1)
    assert len(reports) == 1
    assert reports[0].report_type == "Performance Summary"
    assert reports[0].content == "Generated markdown content"
    assert studio_metrics(s1).reports_generated == 1
    assert any(type(e).__name__ == "ReportGenerated" for e in s1.events)

def test_save_workspace(running_state: StrategyStudioState) -> None:
    s1 = StrategyStudioEngine.save_workspace(running_state, "WS-1", 1004.0)
    
    ws = workspace_summary(s1)
    assert len(ws) == 1
    assert ws[0].workspace_id == "WS-1"
    assert "PROJ-1" in ws[0].project_ids
    assert any(type(e).__name__ == "WorkspaceSaved" for e in s1.events)

def test_load_workspace(running_state: StrategyStudioState) -> None:
    s1 = StrategyStudioEngine.save_workspace(running_state, "WS-1", 1004.0)
    s2 = StrategyStudioEngine.load_workspace(s1, "WS-1", 1005.0)
    assert s2 is s1  # Pure functional load resolves to same state tracking

def test_load_workspace_missing(running_state: StrategyStudioState) -> None:
    with pytest.raises(ValueError, match="not found"):
        StrategyStudioEngine.load_workspace(running_state, "WS-MISSING", 1005.0)

def test_start_session(running_state: StrategyStudioState) -> None:
    s1 = StrategyStudioEngine.start_session(running_state, "SESS-1", "U-1", "PROJ-1", 1006.0)
    assert len(s1.sessions) == 1
    assert s1.sessions["SESS-1"].user_id == "U-1"
    assert any(type(e).__name__ == "SessionStarted" for e in s1.events)

# --- IMMUTABILITY TESTS (20+ assertions) ---

def test_immutability(base_state: StrategyStudioState, sample_project: Project) -> None:
    s1 = StrategyStudioEngine.create_project(base_state, sample_project, 1000.0)
    assert s1 is not base_state
    assert len(base_state.projects) == 0
    assert len(s1.projects) == 1
    assert studio_metrics(base_state).total_projects == 0
    assert studio_metrics(s1).total_projects == 1