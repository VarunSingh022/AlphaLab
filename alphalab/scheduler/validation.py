"""Validation rules ensuring structural integrity of schedules."""

from alphalab.scheduler.exceptions import SchedulerValidationError
from alphalab.scheduler.schedule import ScheduleType
from alphalab.scheduler.scheduler import SESSION_SCHEDULES, is_session_boundary
from alphalab.scheduler.timer import Timer

#: Declared in :class:`ScheduleType` and not implemented. Until v3.10 a timer of
#: one of these types was accepted, fired once at its target, and was never
#: rescheduled -- a repeating schedule that silently stopped repeating (ledger
#: DAT-006). They are refused at registration. ``SESSION_OPEN`` and
#: ``SESSION_CLOSE`` left this set in v3.12, when they were implemented over
#: :class:`~alphalab.data.calendar.MarketCalendar` (ledger SCF-003).
UNIMPLEMENTED_SCHEDULES: frozenset[ScheduleType] = frozenset(
    {
        ScheduleType.CRON,
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
            "parses a cron expression, and a bar's boundary is a convention of its data, "
            "so such a timer would fire once at its target and never again. Use ONE_SHOT "
            "for one firing, INTERVAL with its period, or SESSION_OPEN / SESSION_CLOSE "
            "over the venue's MarketCalendar."
        )

    if timer.schedule_type in SESSION_SCHEDULES:
        if timer.calendar is None:
            raise SchedulerValidationError(
                f"A {timer.schedule_type.name} timer follows a market's trading days and "
                "names no calendar; see alphalab.scheduler.session_timer."
            )
        if not is_session_boundary(timer.calendar, timer.target_timestamp, timer.schedule_type):
            raise SchedulerValidationError(
                f"{timer.target_timestamp!r} is not a {timer.schedule_type.name} instant on "
                f"{timer.calendar.calendar_id}; a session timer fires only at the calendar's "
                "own boundaries. Build it with alphalab.scheduler.session_timer."
            )
    elif timer.calendar is not None:
        raise SchedulerValidationError(
            f"A {timer.schedule_type.name} timer does not follow a calendar; only "
            "SESSION_OPEN and SESSION_CLOSE timers name one."
        )
