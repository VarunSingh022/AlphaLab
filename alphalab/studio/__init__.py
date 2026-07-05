"""AlphaLab Strategy Studio Orchestration Engine."""

from alphalab.studio.adapter import StudioAdapter
from alphalab.studio.backtest import BacktestConfiguration
from alphalab.studio.builder import build_backtest_config, build_pipeline
from alphalab.studio.config import StudioConfig
from alphalab.studio.engine import StrategyStudioEngine
from alphalab.studio.events import (
    BacktestCompleted,
    PipelineExecuted,
    ProjectCreated,
    ReportGenerated,
    SessionStarted,
    StrategyRegistered,
    StudioEvent,
    WorkspaceSaved,
)
from alphalab.studio.exceptions import (
    InvalidStudioStateError,
    StudioError,
    StudioValidationError,
)
from alphalab.studio.manager import StudioManager
from alphalab.studio.metrics import StudioMetrics
from alphalab.studio.pipeline import PipelineDefinition, PipelineStep
from alphalab.studio.project import Project
from alphalab.studio.protocol import StudioComponentProtocol
from alphalab.studio.registry import StudioRegistry
from alphalab.studio.reports import StudioReport
from alphalab.studio.results import (
    BacktestResult,
    ExperimentResult,
    PipelineResult,
    ResearchResult,
)
from alphalab.studio.runner import StudioRunner
from alphalab.studio.sessions import StudioSession
from alphalab.studio.state import StrategyStudioState
from alphalab.studio.strategy import StrategyDefinition
from alphalab.studio.templates import ProjectTemplate
from alphalab.studio.validation import validate_project_creation, validate_project_exists
from alphalab.studio.views import (
    backtest_summary,
    experiment_summary,
    pipeline_summary,
    project_summary,
    report_summary,
    studio_metrics,
    workspace_summary,
)
from alphalab.studio.workspace import WorkspaceSnapshot

__all__ = [
    "BacktestCompleted",
    "BacktestConfiguration",
    "BacktestResult",
    "ExperimentResult",
    "InvalidStudioStateError",
    "PipelineDefinition",
    "PipelineExecuted",
    "PipelineResult",
    "PipelineStep",
    "Project",
    "ProjectCreated",
    "ProjectTemplate",
    "ReportGenerated",
    "ResearchResult",
    "SessionStarted",
    "StrategyDefinition",
    "StrategyRegistered",
    "StrategyStudioEngine",
    "StrategyStudioState",
    "StudioAdapter",
    "StudioComponentProtocol",
    "StudioConfig",
    "StudioError",
    "StudioEvent",
    "StudioManager",
    "StudioMetrics",
    "StudioRegistry",
    "StudioReport",
    "StudioRunner",
    "StudioSession",
    "StudioValidationError",
    "WorkspaceSaved",
    "WorkspaceSnapshot",
    "backtest_summary",
    "build_backtest_config",
    "build_pipeline",
    "experiment_summary",
    "pipeline_summary",
    "project_summary",
    "report_summary",
    "studio_metrics",
    "validate_project_creation",
    "validate_project_exists",
    "workspace_summary",
]
