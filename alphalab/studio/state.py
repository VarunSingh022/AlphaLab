"""Global immutable state container for Strategy Studio."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.studio.config import StudioConfig
from alphalab.studio.events import StudioEvent
from alphalab.studio.metrics import StudioMetrics
from alphalab.studio.project import Project
from alphalab.studio.reports import StudioReport
from alphalab.studio.results import BacktestResult, ExperimentResult, PipelineResult, ResearchResult
from alphalab.studio.sessions import StudioSession
from alphalab.studio.templates import ProjectTemplate
from alphalab.studio.workspace import WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class StrategyStudioState:
    """Deterministic snapshot of the entire Studio workspace."""
    engine_id: str
    config: StudioConfig
    projects: Mapping[str, Project] = field(default_factory=dict)
    sessions: Mapping[str, StudioSession] = field(default_factory=dict)
    templates: Mapping[str, ProjectTemplate] = field(default_factory=dict)
    workspaces: Mapping[str, WorkspaceSnapshot] = field(default_factory=dict)
    backtest_results: Mapping[str, BacktestResult] = field(default_factory=dict)
    research_results: Mapping[str, ResearchResult] = field(default_factory=dict)
    pipeline_results: Mapping[str, PipelineResult] = field(default_factory=dict)
    experiments: Mapping[str, ExperimentResult] = field(default_factory=dict)
    reports: Mapping[str, StudioReport] = field(default_factory=dict)
    metrics: StudioMetrics = field(default_factory=StudioMetrics)
    events: tuple[StudioEvent, ...] = field(default_factory=tuple)