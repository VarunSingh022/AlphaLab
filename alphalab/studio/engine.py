"""Top-level Engine Facade orchestrating Strategy Studio."""

from alphalab.studio.backtest import BacktestConfiguration
from alphalab.studio.config import StudioConfig
from alphalab.studio.manager import StudioManager
from alphalab.studio.pipeline import PipelineDefinition
from alphalab.studio.project import Project
from alphalab.studio.registry import StudioRegistry
from alphalab.studio.runner import StudioRunner
from alphalab.studio.state import StrategyStudioState
from alphalab.studio.strategy import StrategyDefinition


class StrategyStudioEngine:
    """Facade for managing deterministic quantitative research workflows."""

    @staticmethod
    def initialize(engine_id: str, workspace_dir: str = "/workspace") -> StrategyStudioState:
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        config = StudioConfig(engine_id, workspace_dir)
        return StrategyStudioState(engine_id=engine_id, config=config)

    @staticmethod
    def create_project(
        state: StrategyStudioState, project: Project, ts: float
    ) -> StrategyStudioState:
        return StudioRegistry.create_project(state, project, ts)

    @staticmethod
    def register_strategy(
        state: StrategyStudioState, project_id: str, strategy: StrategyDefinition, ts: float
    ) -> StrategyStudioState:
        return StudioRegistry.register_strategy(state, project_id, strategy, ts)

    @staticmethod
    def run_backtest(
        state: StrategyStudioState,
        project_id: str,
        config: BacktestConfiguration,
        simulated_metrics: dict[str, float],
        ts: float,
    ) -> StrategyStudioState:
        return StudioRunner.run_backtest(state, project_id, config, simulated_metrics, ts)

    @staticmethod
    def run_pipeline(
        state: StrategyStudioState,
        project_id: str,
        pipeline: PipelineDefinition,
        simulated_metrics: dict[str, float],
        duration: float,
        ts: float,
    ) -> StrategyStudioState:
        return StudioRunner.run_pipeline(
            state,
            project_id,
            pipeline,
            simulated_metrics,
            duration,
            ts,
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
        return StudioRunner.generate_report(state, project_id, report_type, content, metrics, ts)

    @staticmethod
    def save_workspace(
        state: StrategyStudioState, workspace_id: str, ts: float
    ) -> StrategyStudioState:
        return StudioManager.save_workspace(state, workspace_id, ts)

    @staticmethod
    def load_workspace(
        state: StrategyStudioState, workspace_id: str, ts: float
    ) -> StrategyStudioState:
        return StudioManager.load_workspace(state, workspace_id, ts)

    @staticmethod
    def start_session(
        state: StrategyStudioState, session_id: str, user_id: str, project_id: str, ts: float
    ) -> StrategyStudioState:
        return StudioManager.start_session(state, session_id, user_id, project_id, ts)
