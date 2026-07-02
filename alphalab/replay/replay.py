"""Replay extraction and result models."""

from dataclasses import dataclass

from alphalab.replay.loader import HistoricalEventProtocol
from alphalab.replay.state import ReplayState


@dataclass(frozen=True, slots=True)
class ReplayStepResult:
    """Deterministic output from a single event step."""

    state: ReplayState
    event: HistoricalEventProtocol | None


@dataclass(frozen=True, slots=True)
class ReplayBatchResult:
    """Deterministic output from a timestamp-bounded batch step."""

    state: ReplayState
    events: tuple[HistoricalEventProtocol, ...]
