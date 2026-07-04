"""Immutable domain events describing the Research Engine lifecycle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResearchEvent:
    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class ResearchStarted(ResearchEvent):
    research_id: str
    strategy_id: str


@dataclass(frozen=True, slots=True)
class BiasDetected(ResearchEvent):
    research_id: str
    bias_type: str
    severity: float


@dataclass(frozen=True, slots=True)
class AnalysisCompleted(ResearchEvent):
    research_id: str
    analysis_type: str


@dataclass(frozen=True, slots=True)
class DiagnosticsGenerated(ResearchEvent):
    research_id: str
    warning_count: int


@dataclass(frozen=True, slots=True)
class ResearchCompleted(ResearchEvent):
    research_id: str
    overall_score: float
