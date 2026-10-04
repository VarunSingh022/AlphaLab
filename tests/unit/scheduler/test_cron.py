"""Cron schedules and cron timers (v3.13, ledger DAT-006).

Every expected instant is written as the wall-clock time a reader would check
on a calendar, in the schedule's zone. The daylight-saving cases use New York's
2026 transitions: clocks jump from 02:00 to 03:00 on 8 March and fall back from
02:00 to 01:00 on 1 November.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from alphalab.scheduler import (
    CronSchedule,
    SchedulerEngine,
    SchedulerValidationError,
    ScheduleType,
    Timer,
    cron_timer,
)

NEW_YORK = "America/New_York"


def _instant(local: str, zone: str = NEW_YORK) -> float:
    return datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(zone)).timestamp()


def _local(instant: float, zone: str = NEW_YORK) -> str:
    return datetime.fromtimestamp(instant, tz=UTC).astimezone(ZoneInfo(zone)).isoformat()


def _firings(expression: str, after: str, count: int, zone: str = NEW_YORK) -> list[str]:
    schedule = CronSchedule(expression, zone)
    instant = _instant(after, zone)
    found: list[str] = []
    for _ in range(count):
        following = schedule.next_after(instant)
        assert following is not None
        found.append(_local(following, zone))
        instant = following
    return found


def test_a_schedule_fires_at_the_minutes_it_names_and_never_at_the_instant_given() -> None:
    assert _firings("*/20 9-10 * * *", "2026-06-02T09:00", 4) == [
        "2026-06-02T09:20:00-04:00",
        "2026-06-02T09:40:00-04:00",
        "2026-06-02T10:00:00-04:00",
        "2026-06-02T10:20:00-04:00",
    ]


def test_named_weekdays_skip_the_weekend() -> None:
    assert _firings("30 9 * * mon-FRI", "2026-10-02T10:00", 2) == [
        "2026-10-05T09:30:00-04:00",
        "2026-10-06T09:30:00-04:00",
    ]


def test_two_restricted_day_fields_fire_on_either() -> None:
    """``0 9 1 * MON``: the first of the month, and every Monday."""

    assert _firings("0 9 1 * MON", "2026-06-26T12:00", 3) == [
        "2026-06-29T09:00:00-04:00",  # a Monday
        "2026-07-01T09:00:00-04:00",  # the first, a Wednesday
        "2026-07-06T09:00:00-04:00",  # a Monday
    ]


def test_a_stepped_star_counts_as_unrestricted() -> None:
    """Vixie's rule: ``*/2`` begins with ``*``, so the day of week alone decides."""

    assert _firings("0 12 */2 * SUN", "2026-06-01T00:00", 2) == [
        "2026-06-07T12:00:00-04:00",
        "2026-06-14T12:00:00-04:00",
    ]


def test_sunday_is_both_zero_and_seven() -> None:
    assert CronSchedule("0 0 * * 7", "UTC").days_of_week == frozenset({0})
    assert _firings("0 0 * * 0", "2026-06-01T00:00", 1, "UTC") == ["2026-06-07T00:00:00+00:00"]


def test_the_twenty_ninth_of_february_waits_for_a_leap_year() -> None:
    assert _firings("0 0 29 2 *", "2026-01-01T00:00", 2, "UTC") == [
        "2028-02-29T00:00:00+00:00",
        "2032-02-29T00:00:00+00:00",
    ]


def test_a_minute_inside_the_spring_forward_gap_does_not_fire() -> None:
    assert _firings("30 2 * * *", "2026-03-06T12:00", 3) == [
        "2026-03-07T02:30:00-05:00",
        "2026-03-09T02:30:00-04:00",  # 8 March has no 02:30
        "2026-03-10T02:30:00-04:00",
    ]


def test_a_repeated_minute_fires_once_at_its_first_occurrence() -> None:
    assert _firings("30 1 * * *", "2026-10-31T12:00", 2) == [
        "2026-11-01T01:30:00-04:00",
        "2026-11-02T01:30:00-05:00",
    ]
    # A frequent schedule is silent for the repeated hour's second pass.
    assert _firings("*/30 * * * *", "2026-11-01T00:10", 5) == [
        "2026-11-01T00:30:00-04:00",
        "2026-11-01T01:00:00-04:00",
        "2026-11-01T01:30:00-04:00",
        "2026-11-01T02:00:00-05:00",
        "2026-11-01T02:30:00-05:00",
    ]


def test_inside_the_second_pass_the_first_occurrence_has_already_happened() -> None:
    schedule = CronSchedule("45 1 * * *", NEW_YORK)
    # 01:20 on the second pass, after the clocks went back (EST, fold=1).
    second_pass = datetime(2026, 11, 1, 1, 20, tzinfo=ZoneInfo(NEW_YORK), fold=1).timestamp()
    assert _local(second_pass) == "2026-11-01T01:20:00-05:00"
    following = schedule.next_after(second_pass)
    assert following is not None
    assert _local(following) == "2026-11-02T01:45:00-05:00"


@pytest.mark.parametrize(
    ("expression", "reason"),
    [
        ("* * * *", "five"),
        ("60 * * * *", "outside"),
        ("0 24 * * *", "outside"),
        ("0 0 0 * *", "outside"),
        ("0 0 * 13 *", "outside"),
        ("0 0 * * 8", "outside"),
        ("0 0 * JANUARY *", "not a month"),
        ("10-5 * * * *", "backwards"),
        ("*/0 * * * *", "step of zero"),
        ("5/15 * * * *", "5-59/15"),
        ("0 0 L * *", "not a day-of-month"),
        ("0 0 ? * *", "not a day-of-month"),
        ("0 0 31 4,6 *", "never fire"),
        ("0 0 30 2 *", "never fire"),
    ],
)
def test_what_is_not_in_the_grammar_or_never_fires_is_refused(expression: str, reason: str) -> None:
    with pytest.raises(SchedulerValidationError, match=reason):
        CronSchedule(expression, "UTC")


def test_the_zone_is_required_to_be_a_real_one() -> None:
    with pytest.raises(SchedulerValidationError, match="IANA"):
        CronSchedule("* * * * *", "Mars/Olympus")


def test_a_cron_timer_fires_and_reschedules_itself_on_the_engine() -> None:
    schedule = CronSchedule("0 16 * * MON-FRI", NEW_YORK)
    start = _instant("2026-10-02T12:00")
    state = SchedulerEngine.schedule_timer(
        SchedulerEngine.initialize(start), cron_timer("CLOSE", schedule, start), start
    )
    fired: list[str] = []
    for day in ("2026-10-02T17:00", "2026-10-05T17:00", "2026-10-06T17:00"):
        state, triggered = SchedulerEngine.advance_clock(state, _instant(day))
        assert [event.timer_id for event in triggered] == ["CLOSE"]
        fired.append(day)
    assert _local(state.timers["CLOSE"].target_timestamp) == "2026-10-07T16:00:00-04:00"


def test_a_cron_timer_must_name_its_schedule_and_start_on_it() -> None:
    state = SchedulerEngine.initialize(0.0)
    with pytest.raises(SchedulerValidationError, match="names none"):
        SchedulerEngine.schedule_timer(state, Timer("C", 60.0, ScheduleType.CRON), 0.0)
    off_schedule = Timer("C", 90.0, ScheduleType.CRON, cron=CronSchedule("* * * * *", "UTC"))
    with pytest.raises(SchedulerValidationError, match="not an instant"):
        SchedulerEngine.schedule_timer(state, off_schedule, 0.0)
    stray = Timer("I", 60.0, ScheduleType.INTERVAL, interval=60.0, cron=off_schedule.cron)
    with pytest.raises(SchedulerValidationError, match="only a CRON timer"):
        SchedulerEngine.schedule_timer(state, stray, 0.0)
