"""Validation rules ensuring structural integrity of schedules.

Until v3.10 a timer of a type nothing implemented was accepted, fired once at
its target and was never rescheduled -- a repeating schedule that silently
stopped repeating (ledger DAT-006); from v3.10 such types were refused here.
v3.12 implemented ``SESSION_OPEN`` and ``SESSION_CLOSE`` over a
:class:`~alphalab.data.calendar.MarketCalendar`, and v3.13 ``CRON`` over a
:class:`~alphalab.scheduler.cron.CronSchedule` and removed ``BAR_BOUNDARY``, so
every type is implemented and kept. What is still checked is that a timer
fires where its own schedule says it can.
"""

import math

from alphalab.scheduler.exceptions import SchedulerValidationError
from alphalab.scheduler.schedule import ScheduleType
from alphalab.scheduler.scheduler import SESSION_SCHEDULES, is_session_boundary
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

    if timer.schedule_type is ScheduleType.CRON:
        if timer.cron is None:
            raise SchedulerValidationError(
                "A CRON timer follows a CronSchedule and names none; see "
                "alphalab.scheduler.cron_timer."
            )
        if timer.cron.next_after(math.nextafter(timer.target_timestamp, -math.inf)) != (
            timer.target_timestamp
        ):
            raise SchedulerValidationError(
                f"{timer.target_timestamp!r} is not an instant {timer.cron.expression!r} fires "
                f"at in {timer.cron.zone}; build the timer with alphalab.scheduler.cron_timer."
            )
    elif timer.cron is not None:
        raise SchedulerValidationError(
            f"A {timer.schedule_type.name} timer does not follow a cron schedule; only a "
            "CRON timer names one."
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
