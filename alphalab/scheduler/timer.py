"""Immutable timer models."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from alphalab.scheduler.schedule import ScheduleType


@dataclass(frozen=True, slots=True)
class Timer:
    """Immutable representation of a scheduled timer."""

    timer_id: str
    target_timestamp: float
    schedule_type: ScheduleType
    interval: float | None = None
    cron_expression: str | None = None
    metadata: Mapping[str, Any] | None = None
