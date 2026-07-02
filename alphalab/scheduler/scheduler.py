"""Timer resolution and repeating schedule logic."""

from dataclasses import replace

from alphalab.scheduler.schedule import ScheduleType
from alphalab.scheduler.timer import Timer


class SchedulerResolver:
    """Stateless logic for resolving next trigger times for repeating schedules."""

    @staticmethod
    def resolve_next_timer(timer: Timer, current_time: float) -> Timer | None:
        """
        Calculates the next occurrence of a repeating timer.
        Returns a newly updated Timer instance or None if expired.
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

        return None
