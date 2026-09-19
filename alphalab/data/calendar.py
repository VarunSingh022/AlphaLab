"""When a market is open, in the market's own time.

A timestamp is an instant; a *session* is a local fact. Which trading day
2025-01-02T21:30:00Z belongs to depends entirely on the venue: it is the
afternoon of the 2nd in New York, the late evening of the 2nd in London, and
the early morning of the 3rd in Tokyo. Answering that question requires the
exchange's timezone and its session schedule, which is what a
:class:`MarketCalendar` carries.

AlphaLab ships no holiday data
------------------------------

This module supplies the **mechanism** and not a single holiday, in the same
way v2.11 supplied the security master's mechanism and no taxonomy, and v2.17
supplied the FX rate feed's boundary and no rate. An exchange's holiday list
changes annually, is announced by the exchange, and differs between the cash
and derivatives segments of the same venue. A list baked in here would be
wrong within a year, silently, and would look authoritative while being wrong.

So a caller declares the calendar for their market. Every venue named in the
v3.1 brief -- India, the United States, Europe, Japan, Hong Kong, Singapore,
Australia -- is expressible, including lunch breaks (two windows in a day),
overnight sessions (a window whose close is earlier than its open), half days
(a date with its own windows) and markets that never close.

Not the scheduler's calendar
----------------------------

:class:`alphalab.scheduler.calendar.TradingCalendar` answers a different
question -- "should a job fire today?" -- over UTC weekends and an optional
holiday hook. It knows nothing about venues, sessions or local time, and it is
not what decides whether a market was open when a bar printed.
``tests/regression/test_shared_names_stay_distinct.py`` holds the reason the
two must not be merged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Final

from alphalab.data.exceptions import DataValidationError
from alphalab.data.time import resolve_zone

__all__ = ["CONTINUOUS_SESSION", "MarketCalendar", "SessionWindow"]


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """One continuous stretch of trading, in the exchange's local time.

    A window whose ``closes`` is at or before its ``opens`` runs overnight into
    the following calendar day -- which is how CME's 17:00-16:00 session and
    most FX venues actually work. A day with two windows is a market with a
    lunch break, which is the normal shape in Tokyo, Hong Kong and Singapore.
    """

    opens: time
    closes: time

    @property
    def crosses_midnight(self) -> bool:
        """Whether this window ends on the calendar day after it starts."""

        return self.closes <= self.opens


#: The window of a market that never closes. Expressed as midnight to midnight
#: crossing into the next day, so a 24/7 venue needs no special case anywhere:
#: it is an ordinary overnight window on every weekday.
CONTINUOUS_SESSION: Final = SessionWindow(opens=time(0, 0), closes=time(0, 0))


@dataclass(frozen=True, slots=True)
class MarketCalendar:
    """A venue's timezone and trading schedule.

    Attributes:
        calendar_id: What this calendar is called, e.g. ``"XNSE"``.
        timezone_name: IANA zone the sessions are expressed in. Required, and
            not defaulted: a calendar whose zone is assumed is a calendar that
            silently belongs to whoever wrote the default.
        weekly_sessions: Weekday (0 = Monday) to the windows traded that day.
            A weekday absent from the mapping is not a trading day.
        holidays: Dates the market is closed despite being a trading weekday.
        special_sessions: Dates whose windows replace the usual ones -- half
            days, and the shortened sessions around a festival.
    """

    calendar_id: str
    timezone_name: str
    weekly_sessions: Mapping[int, tuple[SessionWindow, ...]]
    holidays: frozenset[date] = frozenset()
    special_sessions: Mapping[date, tuple[SessionWindow, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.calendar_id.strip():
            raise DataValidationError("A market calendar must be named.")
        resolve_zone(self.timezone_name)
        for weekday in self.weekly_sessions:
            if not 0 <= weekday <= 6:
                raise DataValidationError(
                    f"{self.calendar_id} declares weekday {weekday}; weekdays are 0 (Monday) "
                    "through 6 (Sunday)."
                )

    @classmethod
    def continuous(cls, calendar_id: str, timezone_name: str) -> MarketCalendar:
        """A market that is open every day, all day.

        The honest representation of a crypto venue. It is a real calendar with
        a real timezone -- the zone still decides which local day an instant
        falls in, which matters for daily bars -- and not a special case that
        bypasses session logic.
        """

        return cls(
            calendar_id=calendar_id,
            timezone_name=timezone_name,
            weekly_sessions=dict.fromkeys(range(7), (CONTINUOUS_SESSION,)),
        )

    @property
    def is_continuous(self) -> bool:
        """Whether this calendar has a window covering every day, all day."""

        return all(
            self.weekly_sessions.get(weekday) == (CONTINUOUS_SESSION,) for weekday in range(7)
        )

    def local_datetime(self, timestamp: float) -> datetime:
        """The instant, read as a wall clock in this market's zone."""

        return datetime.fromtimestamp(timestamp, tz=resolve_zone(self.timezone_name))

    def is_trading_day(self, day: date) -> bool:
        """Whether the market trades at all on a local calendar date."""

        return bool(self.windows_on(day))

    def windows_on(self, day: date) -> tuple[SessionWindow, ...]:
        """The windows traded on a local date, after holidays and half days."""

        if day in self.special_sessions:
            return tuple(self.special_sessions[day])
        if day in self.holidays:
            return ()
        return tuple(self.weekly_sessions.get(day.weekday(), ()))

    def session_bounds(self, day: date) -> tuple[float, float] | None:
        """First open and last close on a local date, as Unix seconds.

        ``None`` when the market does not trade that day. For a market with a
        lunch break these are the outer bounds of the day, not a statement that
        it traded continuously between them -- use :meth:`is_open` for that.
        """

        windows = self.windows_on(day)
        if not windows:
            return None
        spans = [self._span(day, window) for window in windows]
        return min(start for start, _ in spans), max(end for _, end in spans)

    def is_open(self, timestamp: float) -> bool:
        """Whether the market was trading at an instant.

        The previous local day is checked as well as the current one, because
        an overnight window that opened yesterday is still open now.
        """

        local = self.local_datetime(timestamp)
        today = local.date()
        for day in (today - timedelta(days=1), today):
            for window in self.windows_on(day):
                start, end = self._span(day, window)
                if start <= timestamp < end:
                    return True
        return False

    def _span(self, day: date, window: SessionWindow) -> tuple[float, float]:
        """A window's absolute bounds, resolving its local times in this zone."""

        zone = resolve_zone(self.timezone_name)
        start = datetime.combine(day, window.opens, tzinfo=zone)
        close_day = day + timedelta(days=1) if window.crosses_midnight else day
        end = datetime.combine(close_day, window.closes, tzinfo=zone)
        return start.timestamp(), end.timestamp()

    def sessions_between(self, start: date, end: date) -> tuple[date, ...]:
        """Every trading date in an inclusive local-date range.

        Raises:
            DataValidationError: If ``end`` precedes ``start``.
        """

        if end < start:
            raise DataValidationError(f"{self.calendar_id}: end {end} precedes start {start}.")
        days: list[date] = []
        cursor = start
        while cursor <= end:
            if self.is_trading_day(cursor):
                days.append(cursor)
            cursor += timedelta(days=1)
        return tuple(days)

    def trading_day_of(self, timestamp: float) -> date | None:
        """The local trading date an instant belongs to.

        For an overnight session this is the date the session *opened* on, not
        the date the wall clock reads -- a trade at 02:00 belongs to the
        session that started the previous evening, which is what a venue's own
        daily statement says. ``None`` when the market was not open.
        """

        local = self.local_datetime(timestamp)
        today = local.date()
        for day in (today - timedelta(days=1), today):
            for window in self.windows_on(day):
                start, end = self._span(day, window)
                if start <= timestamp < end:
                    return day
        return None
