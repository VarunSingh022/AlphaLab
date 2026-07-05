"""Immutable domain events describing the Strategy Studio lifecycle."""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class StudioEvent(BaseEvent):
    pass


@dataclass(frozen=True, slots=True)
class ProjectCreated(StudioEvent):
    project_id: str


@dataclass(frozen=True, slots=True)
class StrategyRegistered(StudioEvent):
    project_id: str
    strategy_id: str


@dataclass(frozen=True, slots=True)
class PipelineExecuted(StudioEvent):
    project_id: str
    pipeline_id: str
    result_id: str


@dataclass(frozen=True, slots=True)
class BacktestCompleted(StudioEvent):
    project_id: str
    backtest_id: str
    result_id: str


@dataclass(frozen=True, slots=True)
class ReportGenerated(StudioEvent):
    project_id: str
    report_id: str


@dataclass(frozen=True, slots=True)
class WorkspaceSaved(StudioEvent):
    workspace_id: str


@dataclass(frozen=True, slots=True)
class SessionStarted(StudioEvent):
    session_id: str
