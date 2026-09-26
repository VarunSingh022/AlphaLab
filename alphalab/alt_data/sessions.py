"""Where an instant falls in a venue's trading day, and when it can first be traded on.

An earnings release at 16:05 in New York is known at 16:05 and cannot be traded
until the next morning's open. One at 08:00 is known before the open and is
tradable at 09:30 the same day. One at 12:00 on a Saturday waits for Monday. The
availability instant says when information exists; the *session* decides when a
strategy trading that venue could first act on it, and a study that anchors on
the first instant it existed rather than the first instant it could be traded
credits the strategy with a trade no one could have made.

:func:`place_in_session` answers both questions at once: which part of the
trading day the instant fell in, as a :class:`SessionTiming`, and the first
instant the venue is open at or after it.

The calendar is reached, not imported
-------------------------------------

:class:`~alphalab.data.calendar.MarketCalendar` is the one calendar authority,
and :mod:`alphalab.data` is a package this one does not import: the data
package's outward edges are pinned, and this package stays a leaf over
:mod:`alphalab.common` so that the research layer can read it.
:class:`SessionCalendar` is therefore a structural protocol listing the four
things placement reads -- the precedent
:class:`~alphalab.conventions.settlement.TradingDayCalendar` set in v3.4 -- and
``MarketCalendar`` satisfies it as it stands. There is no default calendar: a
market that trades around the clock is declared with
``MarketCalendar.continuous``, which is a real calendar and not a special case.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Protocol

from alphalab.alt_data.exceptions import PointInTimeError
from alphalab.alt_data.validation import require_finite_instant
from alphalab.common.point_in_time import StampedRecord, VisibilityRule

__all__ = [
    "SessionCalendar",
    "SessionPlacement",
    "SessionTiming",
    "place_in_session",
    "place_record",
]


class _LocalInstant(Protocol):
    """A wall-clock reading that can say which local date it falls on."""

    def date(self) -> Any: ...


class SessionCalendar(Protocol):
    """What placing an instant in a trading day reads from a calendar.

    :class:`~alphalab.data.calendar.MarketCalendar` satisfies it structurally.
    """

    @property
    def calendar_id(self) -> str: ...

    def is_open(self, timestamp: float) -> bool: ...

    def next_open(self, timestamp: float) -> float | None: ...

    def local_datetime(self, timestamp: float) -> _LocalInstant: ...

    def session_windows_on(self, day: Any) -> tuple[tuple[float, float], ...]: ...


class SessionTiming(Enum):
    """Which part of a venue's trading day an instant fell in."""

    #: The venue was open: the information is tradable at once.
    IN_SESSION = auto()

    #: Before the day's first session opened: tradable at that open.
    BEFORE_OPEN = auto()

    #: Between two sessions of one day -- a lunch break: tradable when the
    #: afternoon session opens.
    BETWEEN_SESSIONS = auto()

    #: After the day's last session closed: tradable at the next session.
    AFTER_CLOSE = auto()

    #: On a local date the venue does not trade -- a weekend or a holiday.
    NON_TRADING_DAY = auto()


@dataclass(frozen=True, slots=True)
class SessionPlacement:
    """An instant placed in a venue's trading day.

    Attributes:
        calendar_id: The venue calendar the placement was made against.
        instant: The instant placed -- typically when information became
            knowable.
        timing: Which part of the trading day it fell in.
        tradable_at: The first instant at or after ``instant`` at which the
            venue is open. Equal to ``instant`` when it was already open.
    """

    calendar_id: str
    instant: float
    timing: SessionTiming
    tradable_at: float


def place_in_session(instant: float, calendar: SessionCalendar) -> SessionPlacement:
    """Place ``instant`` in ``calendar``'s trading day.

    Raises:
        PointInTimeError: If the calendar declares no session opening at or
            after ``instant`` within its search bound -- a calendar that can
            never trade information it was given.
    """

    require_finite_instant(instant, "instant")
    if calendar.is_open(instant):
        return SessionPlacement(calendar.calendar_id, instant, SessionTiming.IN_SESSION, instant)

    opens = calendar.next_open(instant)
    if opens is None:
        raise PointInTimeError(
            f"Calendar {calendar.calendar_id!r} declares no session at or after {instant!r}, "
            "so information known then can never be traded on this venue."
        )

    windows = calendar.session_windows_on(calendar.local_datetime(instant).date())
    if not windows:
        timing = SessionTiming.NON_TRADING_DAY
    elif instant < windows[0][0]:
        timing = SessionTiming.BEFORE_OPEN
    elif instant >= windows[-1][1]:
        timing = SessionTiming.AFTER_CLOSE
    else:
        timing = SessionTiming.BETWEEN_SESSIONS
    return SessionPlacement(calendar.calendar_id, instant, timing, opens)


def place_record(
    record: StampedRecord, visibility: VisibilityRule, calendar: SessionCalendar
) -> SessionPlacement:
    """Place the instant a record became knowable in ``calendar``'s trading day.

    Raises:
        PointInTimeError: If the record has no knowledge instant under
            ``visibility`` -- its availability was never established, or its
            arrival was not recorded under ``INGESTION`` -- so there is no
            instant to place, and none is assumed.
    """

    known = record.stamp.known_at(visibility)
    if known is None:
        raise PointInTimeError(
            f"Record {record.record_id} has no knowledge instant under {visibility.name}, so "
            "it cannot be placed in a trading session. Its observation instant is not a "
            "substitute."
        )
    return place_in_session(known, calendar)
