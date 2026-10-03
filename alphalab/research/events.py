"""Immutable domain events describing the Research Engine lifecycle."""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class ResearchEvent(BaseEvent):
    pass


@dataclass(frozen=True, slots=True)
class ResearchStarted(ResearchEvent):
    research_id: str
    strategy_id: str


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
    """The evaluation finished: how many measurements it made, and how many findings.

    Until v3.12 it carried the blended ``overall_score`` (ledger RES-001), and a
    ``BiasDetected`` event carried a "look-ahead risk" read from a win rate.
    """

    research_id: str
    metric_count: int
    finding_count: int
