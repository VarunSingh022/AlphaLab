"""Immutable definitions for orchestration sequences."""

from dataclasses import dataclass, field
from enum import Enum, auto


class PipelineStep(Enum):
    LOAD_DATA = auto()
    CLEAN_DATA = auto()
    NORMALIZE = auto()
    RESEARCH = auto()
    GENERATE_SIGNALS = auto()
    OPTIMIZE_PORTFOLIO = auto()
    REPLAY = auto()
    REPORT = auto()

@dataclass(frozen=True, slots=True)
class PipelineDefinition:
    pipeline_id: str
    project_id: str
    name: str
    steps: tuple[PipelineStep, ...] = field(default_factory=tuple)