"""Timer resolution and repeating schedule logic.

Session timers (v3.12, ledger SCF-003)
--------------------------------------

A ``SESSION_OPEN`` timer fires at each **trading day's first open** on its
:class:`~alphalab.data.calendar.MarketCalendar`, and a ``SESSION_CLOSE`` timer at
each trading day's **last close** -- the outer bounds
:meth:`~alphalab.data.calendar.MarketCalendar.session_bounds` reports. A market
with a lunch break therefore fires once a day each way, not at the break; an
overnight session opens on the day it starts and closes the next morning; a
holiday or weekend fires nothing. Every instant comes from the calendar, in the
venue's own zone -- the scheduler keeps no calendar of its own (ledger DAT-006).

Until v3.12 these two types were refused at registration, because nothing could
say when a session opened.

Cron timers (v3.13, ledger DAT-006)
-----------------------------------

A ``CRON`` timer fires at every instant its
:class:`~alphalab.scheduler.cron.CronSchedule` names, read on the schedule's own
zone's wall clock -- see :mod:`alphalab.scheduler.cron` for the grammar and the
daylight-saving rule. :func:`cron_timer` builds one at its first firing after a
given instant. ``BAR_BOUNDARY`` is removed: a bar's arrival is the event, and a
timer restating where a bar's boundary falls (:class:`~alphalab.data.time.BarStamp`)
could only disagree with the data.
"""

import math
from dataclasses import replace
from datetime import timedelta

from alphalab.data.calendar import MAX_SESSION_SEARCH_DAYS, MarketCalendar
from alphalab.scheduler.cron import CRON_SEARCH_DAYS, CronSchedule
from alphalab.scheduler.schedule import ScheduleType
from alphalab.scheduler.timer import Timer

#: The schedule types that follow a market's trading days.
SESSION_SCHEDULES = frozenset({ScheduleType.SESSION_OPEN, ScheduleType.SESSION_CLOSE})


def next_session_boundary(
    calendar: MarketCalendar, after: float, kind: ScheduleType
) -> float | None:
    """The first trading-day open (or last close) strictly after ``after``.

    ``None`` when the calendar declares no trading day within
    :data:`~alphalab.data.calendar.MAX_SESSION_SEARCH_DAYS` of ``after``.

    Raises:
        ValueError: If ``kind`` is not a session schedule.
    """
    if kind not in SESSION_SCHEDULES:
        raise ValueError(f"{kind.name} is not a session schedule.")
    # An overnight trading day opened the calendar day before ``after``'s.
    first = calendar.local_datetime(after).date() - timedelta(days=1)
    for offset in range(MAX_SESSION_SEARCH_DAYS + 2):
        bounds = calendar.session_bounds(first + timedelta(days=offset))
        if bounds is None:
            continue
        instant = bounds[0] if kind is ScheduleType.SESSION_OPEN else bounds[1]
        if instant > after:
            return instant
    return None


def is_session_boundary(calendar: MarketCalendar, instant: float, kind: ScheduleType) -> bool:
    """Whether ``instant`` is a trading day's first open (or last close) on ``calendar``."""

    return next_session_boundary(calendar, math.nextafter(instant, -math.inf), kind) == instant


def session_timer(
    timer_id: str,
    kind: ScheduleType,
    calendar: MarketCalendar,
    after: float,
    metadata: dict[str, object] | None = None,
) -> Timer:
    """A session timer whose first firing is the next boundary after ``after``.

    Raises:
        ValueError: If ``kind`` is not a session schedule, or the calendar
            declares no trading day within the search horizon.
    """
    target = next_session_boundary(calendar, after, kind)
    if target is None:
        raise ValueError(
            f"{calendar.calendar_id} declares no trading day within "
            f"{MAX_SESSION_SEARCH_DAYS} days of {after!r}, so a {kind.name} timer has no "
            "instant to fire at."
        )
    return Timer(timer_id, target, kind, metadata=metadata, calendar=calendar)


def cron_timer(
    timer_id: str,
    cron: CronSchedule,
    after: float,
    metadata: dict[str, object] | None = None,
) -> Timer:
    """A cron timer whose first firing is the schedule's next instant after ``after``.

    Raises:
        ValueError: If the schedule names no instant within
            :data:`~alphalab.scheduler.cron.CRON_SEARCH_DAYS` of ``after``.
    """
    target = cron.next_after(after)
    if target is None:
        raise ValueError(
            f"{cron.expression!r} in {cron.zone} fires at no instant within "
            f"{CRON_SEARCH_DAYS} days of {after!r}."
        )
    return Timer(timer_id, target, ScheduleType.CRON, metadata=metadata, cron=cron)


class SchedulerResolver:
    """Stateless logic for resolving next trigger times for repeating schedules."""

    @staticmethod
    def resolve_next_timer(timer: Timer, current_time: float) -> Timer | None:
        """
        Calculates the next occurrence of a repeating timer.
        Returns a newly updated Timer instance or None if expired.

        A session timer whose calendar declares no further trading day within
        the search horizon expires, and so does a cron timer whose schedule
        names no further instant within its own: there is no next instant to
        fire at.
        """
        if timer.schedule_type in {ScheduleType.ONE_SHOT, ScheduleType.MANUAL}:
            return None

        if timer.schedule_type in {ScheduleType.REPEATING, ScheduleType.INTERVAL}:
            if timer.interval is None:
                return None
            # Calculate next interval strictly beyond the current execution boundary
            next_ts = timer.target_timestamp
            while next_ts <= current_time:
                next_ts += timer.interval
            return replace(timer, target_timestamp=next_ts)

        if timer.schedule_type in SESSION_SCHEDULES and timer.calendar is not None:
            following = next_session_boundary(timer.calendar, current_time, timer.schedule_type)
            return None if following is None else replace(timer, target_timestamp=following)

        if timer.schedule_type is ScheduleType.CRON and timer.cron is not None:
            following = timer.cron.next_after(current_time)
            return None if following is None else replace(timer, target_timestamp=following)

        return None
