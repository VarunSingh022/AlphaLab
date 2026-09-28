"""Validation rules ensuring structural integrity of schedules."""

from alphalab.scheduler.exceptions import SchedulerValidationError
from alphalab.scheduler.schedule import ScheduleType
from alphalab.scheduler.timer import Timer

#: Declared in :class:`ScheduleType` and not implemented. Until v3.10 a timer of
#: one of these types was accepted, fired once at its target, and was never
#: rescheduled -- a repeating schedule that silently stopped repeating (ledger
#: DAT-006). They are refused at registration until they are implemented.
UNIMPLEMENTED_SCHEDULES: frozenset[ScheduleType] = frozenset(
    {
        ScheduleType.CRON,
        ScheduleType.SESSION_OPEN,
        ScheduleType.SESSION_CLOSE,
        ScheduleType.BAR_BOUNDARY,
    }
)


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

    if timer.schedule_type in UNIMPLEMENTED_SCHEDULES:
        raise SchedulerValidationError(
            f"{timer.schedule_type.name} timers are declared and not implemented: nothing "
            "parses a cron expression, and the scheduler knows no session or bar boundary, "
            "so such a timer would fire once at its target and never again. Use ONE_SHOT "
            "for one firing or INTERVAL with its period, and take session and bar instants "
            "from alphalab.data.calendar.MarketCalendar."
        )
