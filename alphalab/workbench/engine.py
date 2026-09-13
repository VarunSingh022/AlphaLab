"""Top-level Engine Facade orchestrating AlphaLab Graphical Interfaces.

**How a delegation finds its result.** Strategy Studio mints its own identifier
for every backtest and pipeline run, distinct from the ``backtest_id`` the caller
supplied, and reports it on the event it appends. This facade opens the result
tab under that identifier, so it has to read it back.

Until v2.16 it read it back by filtering Studio's *entire* event log with
``type(e).__name__ == "BacktestCompleted"`` and taking the last match. That was
wrong twice over. It was O(events) per delegation, so a session of ``N``
delegations cost ``O(N^2)`` -- ``benchmarks/benchmark_workbench.py`` measured
throughput halving on every doubling of its workload and could not complete the
100,000 iterations it declares. And it matched a class *name*, so an event of
that name from any other package would have been accepted as Studio's.

A delegation appends its own events to the end of the log and knows where the
log ended before the call, so the events it produced are the ones past that
mark: a bounded read, by type, of what this call actually did.
"""

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.ids import new_id
from alphalab.studio import (
    BacktestCompleted,
    BacktestConfiguration,
    PipelineDefinition,
    PipelineExecuted,
    StrategyStudioEngine,
    StrategyStudioState,
    StudioEvent,
)
from alphalab.workbench.events import WorkbenchStarted
from alphalab.workbench.layout import WorkspaceLayout
from alphalab.workbench.manager import WorkbenchManager
from alphalab.workbench.panels import Panel, PanelType
from alphalab.workbench.registry import WorkbenchRegistry
from alphalab.workbench.state import WorkbenchState
from alphalab.workbench.workspace import WorkbenchConfig


def _events_appended_by(
    before: StrategyStudioState, after: StrategyStudioState
) -> tuple[StudioEvent, ...]:
    """The events one delegation appended, and only those.

    Bounded by the call rather than by the log: reading the whole log to find
    the newest match is what made a Workbench session quadratic.
    """

    return tuple(after.events[index] for index in range(len(before.events), len(after.events)))


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
            workbench_id=workbench_id,
            config=config,
            active_layout=default_layout,
            events=AppendOnlyLog((evt,)),
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
        """Delegates logic to Studio, then updates UI to reflect the execution.

        The tab this opens is :func:`~alphalab.workbench.views.active_tab` on the
        returned state; its identifier is Studio's minted ``result_id``, not the
        caller's ``backtest_id``, and the two are not interchangeable.
        """
        # 1. Delegate to the backend Engine
        new_studio_state = StrategyStudioEngine.run_backtest(
            studio_state, project_id, config, simulated_metrics, ts
        )

        # 2. Read the result ID off the event this call appended
        completed = [
            e
            for e in _events_appended_by(studio_state, new_studio_state)
            if isinstance(e, BacktestCompleted)
        ]
        res_id = completed[-1].result_id if completed else config.backtest_id

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
        """Delegates pipeline orchestration and opens the pipeline tracker UI.

        The tab this opens is :func:`~alphalab.workbench.views.active_tab` on the
        returned state, named after Studio's minted ``result_id``.
        """
        # 1. Delegate
        new_studio_state = StrategyStudioEngine.run_pipeline(
            studio_state, project_id, pipeline, simulated_metrics, duration, ts
        )

        # 2. Read the result ID off the event this call appended
        executed = [
            e
            for e in _events_appended_by(studio_state, new_studio_state)
            if isinstance(e, PipelineExecuted)
        ]
        res_id = executed[-1].result_id if executed else pipeline.pipeline_id

        # 3. Update UI
        new_wb_state = WorkbenchManager.open_tab(
            wb_state, f"pipe-{res_id}", "Pipeline Run", res_id, ts
        )

        return new_wb_state, new_studio_state
