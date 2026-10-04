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
class ScheduledSession:
    """One dated session the scheduler tracks: its phase between two instants.

    Named ``TradingSession`` until v3.13, the name of the runtime's session
    driver (:class:`alphalab.runtime.TradingSession`); a venue's weekly
    time-of-day window is :class:`alphalab.data.calendar.SessionWindow`, which
    this is not -- it is one occurrence, in epoch seconds (ledger API-001).
    """

    session_id: str
    start_time: float
    end_time: float
    phase: SessionPhase
