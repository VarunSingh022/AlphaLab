"""Pure queries exposing transparent Strategy Studio State access."""

from collections.abc import Sequence

from alphalab.studio.metrics import StudioMetrics
from alphalab.studio.project import Project
from alphalab.studio.reports import StudioReport
from alphalab.studio.results import BacktestResult, ExperimentResult, PipelineResult
from alphalab.studio.state import StrategyStudioState
from alphalab.studio.workspace import WorkspaceSnapshot


def project_summary(state: StrategyStudioState) -> Sequence[Project]:
    return tuple(state.projects.values())

def workspace_summary(state: StrategyStudioState) -> Sequence[WorkspaceSnapshot]:
    return tuple(state.workspaces.values())

def experiment_summary(state: StrategyStudioState) -> Sequence[ExperimentResult]:
    return tuple(state.experiments.values())

def pipeline_summary(state: StrategyStudioState) -> Sequence[PipelineResult]:
    return tuple(state.pipeline_results.values())

def backtest_summary(state: StrategyStudioState) -> Sequence[BacktestResult]:
    return tuple(state.backtest_results.values())

def report_summary(state: StrategyStudioState) -> Sequence[StudioReport]:
    return tuple(state.reports.values())

def studio_metrics(state: StrategyStudioState) -> StudioMetrics:
    return state.metrics