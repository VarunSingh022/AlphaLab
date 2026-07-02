"""Validation rules ensuring structural integrity of schedules."""

from alphalab.scheduler.exceptions import SchedulerValidationError
from alphalab.scheduler.schedule import ScheduleType
from alphalab.scheduler.timer import Timer


def validate_timer(timer: Timer, current_time: float) -> None:
    """Validates structural and temporal integrity of a timer prior to registration."""
    if not timer.timer_id:
        raise SchedulerValidationError("Timer must have a valid timer_id.")

    if timer.target_timestamp < 0.0:
        raise SchedulerValidationError("Timer target timestamp cannot be negative.")

    if timer.target_timestamp < current_time:
        raise SchedulerValidationError("Cannot schedule a timer in the past.")

    if timer.schedule_type in {ScheduleType.REPEATING, ScheduleType.INTERVAL} and (
        timer.interval is None or timer.interval <= 0.0
    ):
        raise SchedulerValidationError("Repeating/Interval timers require a positive interval.")

    if timer.schedule_type == ScheduleType.CRON and not timer.cron_expression:
        raise SchedulerValidationError("Cron timers require a cron_expression.")
