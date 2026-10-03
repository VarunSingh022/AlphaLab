"""Immutable timer models."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from alphalab.data.calendar import MarketCalendar
from alphalab.scheduler.schedule import ScheduleType


@dataclass(frozen=True, slots=True)
class Timer:
    """Immutable representation of a scheduled timer.

    Attributes:
        timer_id: Unique within one scheduler.
        target_timestamp: When it next fires, in Unix seconds.
        schedule_type: How it repeats.
        interval: The period of an ``INTERVAL`` or ``REPEATING`` timer.
        cron_expression: Carried for a ``CRON`` timer, which is refused at
            registration: nothing parses a cron expression.
        metadata: The caller's own attributes.
        calendar: The market whose trading day a ``SESSION_OPEN`` or
            ``SESSION_CLOSE`` timer follows -- required for those and refused
            for every other kind (v3.12, ledger SCF-003). See
            :mod:`alphalab.scheduler.scheduler` for which instants they fire at.
    """

    timer_id: str
    target_timestamp: float
    schedule_type: ScheduleType
    interval: float | None = None
    cron_expression: str | None = None
    metadata: Mapping[str, Any] | None = None
    calendar: MarketCalendar | None = None
