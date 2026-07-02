"""AlphaLab Backtesting & Replay Engine."""

from alphalab.replay.engine import ReplayEngine
from alphalab.replay.events import (
    ReplayAdvanced,
    ReplayCompleted,
    ReplayPaused,
    ReplayReset,
    ReplayResumed,
    ReplayStarted,
    ReplayStopped,
    ReplaySystemEvent,
)
from alphalab.replay.exceptions import (
    InvalidReplayStateError,
    ReplayError,
    ReplayValidationError,
)
from alphalab.replay.loader import DataLoaderProtocol, HistoricalEventProtocol
from alphalab.replay.metrics import ReplayMetrics
from alphalab.replay.replay import ReplayBatchResult, ReplayStepResult
from alphalab.replay.session import ReplaySession
from alphalab.replay.state import ReplayState, ReplayStatus
from alphalab.replay.validation import validate_events, validate_session
from alphalab.replay.views import (
    completion_ratio,
    current_throughput,
    current_timestamp,
    elapsed_real_time,
    processed_events,
    remaining_events,
)

__all__ = [
    "DataLoaderProtocol",
    "HistoricalEventProtocol",
    "InvalidReplayStateError",
    "ReplayAdvanced",
    "ReplayBatchResult",
    "ReplayCompleted",
    "ReplayEngine",
    "ReplayError",
    "ReplayMetrics",
    "ReplayPaused",
    "ReplayReset",
    "ReplayResumed",
    "ReplaySession",
    "ReplayStarted",
    "ReplayState",
    "ReplayStatus",
    "ReplayStepResult",
    "ReplayStopped",
    "ReplaySystemEvent",
    "ReplayValidationError",
    "completion_ratio",
    "current_throughput",
    "current_timestamp",
    "elapsed_real_time",
    "processed_events",
    "remaining_events",
    "validate_events",
    "validate_session",
]
