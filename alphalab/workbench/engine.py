"""Top-level Engine Facade orchestrating AlphaLab Graphical Interfaces."""

from alphalab.common.ids import new_id
from alphalab.studio import (
    BacktestConfiguration,
    PipelineDefinition,
    StrategyStudioEngine,
    StrategyStudioState,
)
from alphalab.workbench.events import WorkbenchStarted
from alphalab.workbench.layout import WorkspaceLayout
from alphalab.workbench.manager import WorkbenchManager
from alphalab.workbench.panels import Panel, PanelType
from alphalab.workbench.registry import WorkbenchRegistry
from alphalab.workbench.state import WorkbenchState
from alphalab.workbench.workspace import WorkbenchConfig


class WorkbenchEngine:
    """Facade orchestrating UI transitions AND delegating to Strategy Studio."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def initialize(workbench_id: str, ts: float) -> WorkbenchState:
        if not workbench_id.strip():
            raise ValueError("Workbench ID cannot be empty.")

        config = WorkbenchConfig(workbench_id)
        default_layout = WorkspaceLayout(
            "DEFAULT",
            "System Default",
            (Panel("P1", PanelType.DASHBOARD), Panel("P2", PanelType.PROJECTS)),
        )

        evt = WorkbenchStarted(WorkbenchEngine._create_id(), ts, workbench_id)
        return WorkbenchState(
            workbench_id=workbench_id, config=config, active_layout=default_layout, events=(evt,)
        )

    # ---------------------------------------------------------
    # PURE UI TRANSITIONS
    # ---------------------------------------------------------

    @staticmethod
    def open_project(wb_state: WorkbenchState, project_id: str, ts: float) -> WorkbenchState:
        return WorkbenchManager.open_project(wb_state, project_id, ts)

    @staticmethod
    def close_project(wb_state: WorkbenchState, ts: float) -> WorkbenchState:
        return WorkbenchManager.close_project(wb_state, ts)

    @staticmethod
    def open_dataset(wb_state: WorkbenchState, dataset_id: str, ts: float) -> WorkbenchState:
        return WorkbenchManager.open_tab(
            wb_state, f"ds-{dataset_id}", f"Dataset: {dataset_id}", dataset_id, ts
        )

    @staticmethod
    def show_report(wb_state: WorkbenchState, report_id: str, ts: float) -> WorkbenchState:
        return WorkbenchManager.open_tab(
            wb_state, f"rep-{report_id}", f"Report: {report_id}", report_id, ts
        )

    @staticmethod
    def save_layout(
        wb_state: WorkbenchState, layout_id: str, name: str, ts: float
    ) -> WorkbenchState:
        return WorkbenchRegistry.save_layout(wb_state, layout_id, name, ts)

    @staticmethod
    def restore_layout(wb_state: WorkbenchState, layout_id: str, ts: float) -> WorkbenchState:
        return WorkbenchRegistry.restore_layout(wb_state, layout_id, ts)

    # ---------------------------------------------------------
    # ORCHESTRATION DELEGATIONS TO STRATEGY STUDIO
    # ---------------------------------------------------------

    @staticmethod
    def run_backtest(
        wb_state: WorkbenchState,
        studio_state: StrategyStudioState,
        project_id: str,
        config: BacktestConfiguration,
        simulated_metrics: dict[str, float],
        ts: float,
    ) -> tuple[WorkbenchState, StrategyStudioState]:
        """Delegates logic to Studio, then updates UI to reflect the execution."""
        # 1. Delegate to the backend Engine
        new_studio_state = StrategyStudioEngine.run_backtest(
            studio_state, project_id, config, simulated_metrics, ts
        )

        # 2. Extract generated result ID to open the relevant UI tab
        result_events = [
            e for e in new_studio_state.events if type(e).__name__ == "BacktestCompleted"
        ]
        res_id = getattr(result_events[-1], "result_id", config.backtest_id)

        # 3. Update UI to point to the new data
        new_wb_state = WorkbenchManager.open_tab(
            wb_state, f"bt-{res_id}", "Backtest Results", res_id, ts
        )

        return new_wb_state, new_studio_state

    @staticmethod
    def run_pipeline(
        wb_state: WorkbenchState,
        studio_state: StrategyStudioState,
        project_id: str,
        pipeline: PipelineDefinition,
        simulated_metrics: dict[str, float],
        duration: float,
        ts: float,
    ) -> tuple[WorkbenchState, StrategyStudioState]:
        """Delegates pipeline orchestration and opens the pipeline tracker UI."""
        # 1. Delegate
        new_studio_state = StrategyStudioEngine.run_pipeline(
            studio_state, project_id, pipeline, simulated_metrics, duration, ts
        )

        # 2. Extract Result ID
        result_events = [
            e for e in new_studio_state.events if type(e).__name__ == "PipelineExecuted"
        ]
        res_id = getattr(result_events[-1], "result_id", pipeline.pipeline_id)

        # 3. Update UI
        new_wb_state = WorkbenchManager.open_tab(
            wb_state, f"pipe-{res_id}", "Pipeline Run", res_id, ts
        )

        return new_wb_state, new_studio_state
