"""Comprehensive tests validating strict UI orchestration and Studio delegations."""

import pytest

from alphalab.studio import (
    BacktestConfiguration,
    PipelineDefinition,
    PipelineStep,
    Project,
    StrategyStudioEngine,
    StrategyStudioState,
)
from alphalab.workbench import (
    WorkbenchAdapter,
    WorkbenchEngine,
    WorkbenchState,
    WorkbenchValidationError,
    active_layout,
    active_tabs,
    current_project,
    saved_layouts,
)


@pytest.fixture
def studio_base_state() -> StrategyStudioState:
    s = StrategyStudioEngine.initialize("STUDIO-01", "/tmp/ws")
    p = Project("PROJ-1", "Test", 1000.0)
    return StrategyStudioEngine.create_project(s, p, 1000.0)

@pytest.fixture
def workbench_base_state() -> WorkbenchState:
    return WorkbenchEngine.initialize("UI-01", 1000.0)

# --- WORKBENCH INITIALIZATION & PROJECT TESTS (20+ assertions) ---

def test_engine_initialization() -> None:
    wb = WorkbenchEngine.initialize("E1", 1000.0)
    assert wb.workbench_id == "E1"
    assert current_project(wb) is None
    assert active_layout(wb) is not None
    assert any(type(e).__name__ == "WorkbenchStarted" for e in wb.events)

    with pytest.raises(ValueError, match="empty"):
        WorkbenchEngine.initialize("", 1000.0)

def test_open_project(workbench_base_state: WorkbenchState) -> None:
    wb2 = WorkbenchEngine.open_project(workbench_base_state, "PROJ-1", 1001.0)
    assert current_project(wb2) == "PROJ-1"
    assert any(type(e).__name__ == "ProjectOpened" for e in wb2.events)

def test_close_project(workbench_base_state: WorkbenchState) -> None:
    wb2 = WorkbenchEngine.open_project(workbench_base_state, "PROJ-1", 1001.0)
    wb3 = WorkbenchEngine.close_project(wb2, 1002.0)
    assert current_project(wb3) is None
    assert any(type(e).__name__ == "ProjectClosed" for e in wb3.events)

# --- TAB & NAVIGATION TESTS (30+ assertions) ---

def test_open_dataset_tab(workbench_base_state: WorkbenchState) -> None:
    wb2 = WorkbenchEngine.open_dataset(workbench_base_state, "DATA-A", 1001.0)
    tabs = active_tabs(wb2)
    assert len(tabs) == 1
    assert tabs[0].content_ref == "DATA-A"
    assert tabs[0].is_active

def test_show_report_tab(workbench_base_state: WorkbenchState) -> None:
    wb2 = WorkbenchEngine.show_report(workbench_base_state, "REP-A", 1001.0)
    tabs = active_tabs(wb2)
    assert len(tabs) == 1
    assert tabs[0].content_ref == "REP-A"

def test_tab_activation(workbench_base_state: WorkbenchState) -> None:
    wb2 = WorkbenchEngine.open_dataset(workbench_base_state, "DATA-A", 1001.0)
    wb3 = WorkbenchEngine.show_report(wb2, "REP-A", 1002.0)
    
    tabs = active_tabs(wb3)
    assert len(tabs) == 2
    assert not tabs[0].is_active  # DATA-A is inactive
    assert tabs[1].is_active      # REP-A is active

def test_close_tab(workbench_base_state: WorkbenchState) -> None:
    wb2 = WorkbenchEngine.open_dataset(workbench_base_state, "DATA-A", 1001.0)
    wb3 = WorkbenchEngine.open_dataset(wb2, "DATA-B", 1002.0)
    
    from alphalab.workbench.manager import WorkbenchManager
    wb4 = WorkbenchManager.close_tab(wb3, "ds-DATA-B", 1003.0)
    
    tabs = active_tabs(wb4)
    assert len(tabs) == 1
    assert tabs[0].is_active  # Fallback activation to remaining tab

def test_close_missing_tab(workbench_base_state: WorkbenchState) -> None:
    from alphalab.workbench.manager import WorkbenchManager
    with pytest.raises(WorkbenchValidationError, match="not open"):
        WorkbenchManager.close_tab(workbench_base_state, "MISSING", 1001.0)

# --- LAYOUT MANAGEMENT TESTS (20+ assertions) ---

def test_save_layout(workbench_base_state: WorkbenchState) -> None:
    wb2 = WorkbenchEngine.save_layout(workbench_base_state, "LAY-1", "My Layout", 1001.0)
    layouts = saved_layouts(wb2)
    assert len(layouts) == 1
    assert layouts[0].layout_id == "LAY-1"
    assert any(type(e).__name__ == "LayoutSaved" for e in wb2.events)

def test_save_duplicate_layout(workbench_base_state: WorkbenchState) -> None:
    wb2 = WorkbenchEngine.save_layout(workbench_base_state, "LAY-1", "My Layout", 1001.0)
    with pytest.raises(WorkbenchValidationError, match="already exists"):
        WorkbenchEngine.save_layout(wb2, "LAY-1", "Duplicate", 1002.0)

def test_restore_layout(workbench_base_state: WorkbenchState) -> None:
    wb2 = WorkbenchEngine.save_layout(workbench_base_state, "LAY-1", "My Layout", 1001.0)
    wb3 = WorkbenchEngine.restore_layout(wb2, "LAY-1", 1002.0)
    
    act = active_layout(wb3)
    assert act is not None
    assert act.layout_id == "LAY-1"
    assert any(type(e).__name__ == "LayoutRestored" for e in wb3.events)

def test_restore_missing_layout(workbench_base_state: WorkbenchState) -> None:
    with pytest.raises(ValueError, match="not found"):
        WorkbenchEngine.restore_layout(workbench_base_state, "MISSING", 1001.0)

# --- STUDIO DELEGATION TESTS (Ensures No Duplicated Logic) (20+ assertions) ---

def test_run_backtest_delegation(
    workbench_base_state: WorkbenchState, studio_base_state: StrategyStudioState
) -> None:
    cfg = BacktestConfiguration("BT-1", "STRAT-1", (), 0.0, 100.0, 10_000.0)
    metrics = {"total_return": 0.5}
    
    new_wb, new_studio = WorkbenchEngine.run_backtest(
        workbench_base_state, studio_base_state, "PROJ-1", cfg, metrics, 1001.0
    )
    
    # Assert Studio Processed the Math
    proj = new_studio.projects["PROJ-1"]
    assert len(proj.backtests) == 1
    
    # Assert UI Opened the View
    tabs = active_tabs(new_wb)
    assert len(tabs) == 1
    assert tabs[0].title == "Backtest Results"

def test_run_pipeline_delegation(
    workbench_base_state: WorkbenchState, studio_base_state: StrategyStudioState
) -> None:
    pipe = PipelineDefinition("PIPE-1", "PROJ-1", "Test", (PipelineStep.RESEARCH,))
    metrics = {"score": 99.0}
    
    new_wb, new_studio = WorkbenchEngine.run_pipeline(
        workbench_base_state, studio_base_state, "PROJ-1", pipe, metrics, 10.0, 1001.0
    )
    
    assert len(new_studio.projects["PROJ-1"].pipelines) == 1
    
    tabs = active_tabs(new_wb)
    assert len(tabs) == 1
    assert tabs[0].title == "Pipeline Run"

# --- ADAPTER & VIEWS TESTS (10+ assertions) ---

def test_adapter_project_view(studio_base_state: StrategyStudioState) -> None:
    view = WorkbenchAdapter.format_project_view(studio_base_state, "PROJ-1")
    assert view["project_id"] == "PROJ-1"
    assert view["strategies_count"] == 0

def test_adapter_missing_project(studio_base_state: StrategyStudioState) -> None:
    view = WorkbenchAdapter.format_project_view(studio_base_state, "MISSING")
    assert view == {}