"""A timer type the scheduler cannot keep is refused, not fired once (ledger DAT-006).

``ScheduleType`` declares CRON, SESSION_OPEN, SESSION_CLOSE and BAR_BOUNDARY, and
until v3.10 nothing implemented them: nothing parsed a cron expression and the
scheduler knew no session or bar boundary. A timer of one of those types was
accepted, fired once at its target, and was never rescheduled -- the audit's
probe registered a CRON timer and found none left after its first firing.
"""

import pytest

from alphalab.scheduler import SchedulerEngine, ScheduleType, Timer
from alphalab.scheduler.exceptions import SchedulerValidationError
from alphalab.scheduler.validation import UNIMPLEMENTED_SCHEDULES

UNIMPLEMENTED: list[ScheduleType] = sorted(UNIMPLEMENTED_SCHEDULES, key=lambda kind: kind.name)


@pytest.mark.parametrize("kind", UNIMPLEMENTED)
def test_an_unimplemented_timer_is_refused_at_registration(kind: ScheduleType) -> None:
    state = SchedulerEngine.initialize(0.0)
    timer = Timer("T-1", 100.0, kind, interval=60.0, cron_expression="*/5 * * * *")

    with pytest.raises(SchedulerValidationError, match="not implemented"):
        SchedulerEngine.schedule_timer(state, timer, 0.0)


def test_the_implemented_types_are_still_accepted() -> None:
    state = SchedulerEngine.initialize(0.0)
    for index, kind in enumerate(
        (ScheduleType.ONE_SHOT, ScheduleType.INTERVAL, ScheduleType.REPEATING, ScheduleType.MANUAL)
    ):
        state = SchedulerEngine.schedule_timer(
            state, Timer(f"T-{index}", 100.0, kind, interval=60.0), 0.0
        )

    assert set(ScheduleType) - UNIMPLEMENTED_SCHEDULES == {
        ScheduleType.ONE_SHOT,
        ScheduleType.INTERVAL,
        ScheduleType.REPEATING,
        ScheduleType.MANUAL,
    }
