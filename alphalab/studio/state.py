"""Global immutable state container for Strategy Studio.

The keyed indexes and the event log use the canonical containers from
:mod:`alphalab.common`, for the reason v2.1 and v2.2 introduced them.

Until v2.16 every mutator here rebuilt a whole ``dict`` and a whole ``tuple``
per transition -- ``dict(state.backtest_results)``, ``(*state.events, evt)`` --
so tracking ``N`` backtests copied ``O(N^2)`` entries three times over, and
``benchmarks/benchmark_workbench.py`` could not complete its declared 100,000
iteration workload. That is the same defect ``AppendOnlyLog`` fixed for the
execution path's histories and ``PersistentMap`` fixed for the OMS order book;
this package simply never took either. The value semantics are unchanged: both
containers are immutable and every mutation returns a new one.
"""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
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
    projects: PersistentMap[str, Project] = field(default_factory=PersistentMap)
    sessions: PersistentMap[str, StudioSession] = field(default_factory=PersistentMap)
    templates: PersistentMap[str, ProjectTemplate] = field(default_factory=PersistentMap)
    workspaces: PersistentMap[str, WorkspaceSnapshot] = field(default_factory=PersistentMap)
    backtest_results: PersistentMap[str, BacktestResult] = field(default_factory=PersistentMap)
    research_results: PersistentMap[str, ResearchResult] = field(default_factory=PersistentMap)
    pipeline_results: PersistentMap[str, PipelineResult] = field(default_factory=PersistentMap)
    experiments: PersistentMap[str, ExperimentResult] = field(default_factory=PersistentMap)
    reports: PersistentMap[str, StudioReport] = field(default_factory=PersistentMap)
    metrics: StudioMetrics = field(default_factory=StudioMetrics)
    events: AppendOnlyLog[StudioEvent] = field(default_factory=AppendOnlyLog)
