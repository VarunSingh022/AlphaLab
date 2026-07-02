"""Trading session and phase models."""

from dataclasses import dataclass
from enum import Enum, auto


class SessionPhase(Enum):
    """Phases defining the lifecycle of a single trading day."""

    PRE_MARKET = auto()
    REGULAR_SESSION = auto()
    POST_MARKET = auto()
    CLOSED = auto()


@dataclass(frozen=True, slots=True)
class TradingSession:
    """Immutable representation of an active or scheduled trading session window."""

    session_id: str
    start_time: float
    end_time: float
    phase: SessionPhase
