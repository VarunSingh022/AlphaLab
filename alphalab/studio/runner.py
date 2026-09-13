"""Execution facades orchestrating simulated downstream AlphaLab engine calls."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.studio.adapter import StudioAdapter
from alphalab.studio.backtest import BacktestConfiguration
from alphalab.studio.events import BacktestCompleted, PipelineExecuted, ReportGenerated
from alphalab.studio.pipeline import PipelineDefinition
from alphalab.studio.reports import StudioReport
from alphalab.studio.state import StrategyStudioState
from alphalab.studio.validation import validate_project_exists


class StudioRunner:
    """Stateless orchestrator that tracks and logs analytical workloads."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def run_backtest(
        state: StrategyStudioState,
        project_id: str,
        config: BacktestConfiguration,
        simulated_metrics: dict[str, float],
        ts: float,
    ) -> StrategyStudioState:
        validate_project_exists(state, project_id)

        # Track the configuration in the project
        proj = state.projects[project_id]
        updated_proj = replace(proj, backtests=proj.backtests.append(config))

        # Generate the mapped tracking result
        res_id = StudioRunner._create_id()
        result = StudioAdapter.to_backtest_result(
            res_id, config.backtest_id, config.strategy_id, simulated_metrics
        )

        mets = replace(state.metrics, backtests_run=state.metrics.backtests_run + 1)
        evt = BacktestCompleted(
            StudioRunner._create_id(),
            ts,
            project_id,
            config.backtest_id,
            res_id,
        )

        return replace(
            state,
            projects=state.projects.set(project_id, updated_proj),
            backtest_results=state.backtest_results.set(res_id, result),
            metrics=mets,
            events=state.events.append(evt),
        )

    @staticmethod
    def run_pipeline(
        state: StrategyStudioState,
        project_id: str,
        pipeline: PipelineDefinition,
        simulated_metrics: dict[str, float],
        duration: float,
        ts: float,
    ) -> StrategyStudioState:
        validate_project_exists(state, project_id)

        proj = state.projects[project_id]
        updated_proj = replace(proj, pipelines=proj.pipelines.append(pipeline))

        res_id = StudioRunner._create_id()
        result = StudioAdapter.to_pipeline_result(
            res_id, pipeline.pipeline_id, True, duration, simulated_metrics
        )

        mets = replace(state.metrics, pipelines_executed=state.metrics.pipelines_executed + 1)
        evt = PipelineExecuted(
            StudioRunner._create_id(),
            ts,
            project_id,
            pipeline.pipeline_id,
            res_id,
        )

        return replace(
            state,
            projects=state.projects.set(project_id, updated_proj),
            pipeline_results=state.pipeline_results.set(res_id, result),
            metrics=mets,
            events=state.events.append(evt),
        )

    @staticmethod
    def generate_report(
        state: StrategyStudioState,
        project_id: str,
        report_type: str,
        content: str,
        metrics: tuple[float, ...],
        ts: float,
    ) -> StrategyStudioState:
        validate_project_exists(state, project_id)

        report_id = StudioRunner._create_id()
        report = StudioReport(report_id, project_id, report_type, ts, content, metrics)

        mets = replace(state.metrics, reports_generated=state.metrics.reports_generated + 1)
        evt = ReportGenerated(StudioRunner._create_id(), ts, project_id, report_id)

        return replace(
            state,
            reports=state.reports.set(report_id, report),
            metrics=mets,
            events=state.events.append(evt),
        )
