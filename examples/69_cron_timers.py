"""
AlphaLab Examples
=================

Example 69 : Cron Timers on a Zone's Wall Clock

Difficulty : Intermediate

Estimated Time : 8 minutes

Prerequisites
-------------

✓ Example 32 (exchange calendars and sessions)

Topics
------

• A five-field cron expression read on a stated IANA zone's wall clock
• Day of month and day of week: either, when both are restricted
• Spring forward: a minute that does not exist does not fire
• Fall back: a minute that occurs twice fires once
• Expressions refused when they are written, not when they would have fired
• A cron timer on the scheduler engine: fired, rescheduled, and fired once
  when the clock jumps past several of its instants

What this shows
---------------

Until v3.13 ``ScheduleType.CRON`` was declared and refused: nothing parsed an
expression, and a timer carried a string nobody read. A ``CronSchedule`` is the
expression parsed once, with the zone whose wall clock it is read on, and the
engine reschedules a cron timer to the schedule's next instant after each
firing. Where cron implementations differ -- how the two day fields combine,
what a daylight-saving transition does -- the rule is stated, and shown here
on New York's 2026 transitions.

Run

    python examples/69_cron_timers.py
"""

import textwrap
from datetime import UTC, datetime

from alphalab.data.time import resolve_zone
from alphalab.scheduler import (
    CronSchedule,
    SchedulerEngine,
    SchedulerValidationError,
    ScheduleType,
    Timer,
    cron_timer,
    next_timer,
)

NEW_YORK = "America/New_York"


def rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def refused(label: str, error: Exception) -> None:
    print(f"  {label}: refused --")
    print(textwrap.fill(str(error), width=74, initial_indent="    ", subsequent_indent="    "))


def at(local: str, zone: str = NEW_YORK) -> float:
    """A wall-clock time in ``zone`` (one that occurs once) as Unix seconds."""

    return datetime.fromisoformat(local).replace(tzinfo=resolve_zone(zone)).timestamp()


def shown(instant: float, zone: str = NEW_YORK) -> str:
    """An instant as the zone's wall clock reads it, and as UTC does."""

    universal = datetime.fromtimestamp(instant, tz=UTC)
    wall = universal.astimezone(resolve_zone(zone))
    return f"{wall:%a %d %b %H:%M %Z}  ({universal:%H:%M} UTC)"


def firings(schedule: CronSchedule, after: str, count: int) -> list[str]:
    """The schedule's next ``count`` instants after a wall-clock time."""

    instant, found = at(after, schedule.zone), []
    for _ in range(count):
        following = schedule.next_after(instant)
        if following is None:
            break
        found.append(shown(following, schedule.zone))
        instant = following
    return found


def main() -> None:
    print("=" * 72)
    print("AlphaLab Example 69 : Cron Timers on a Zone's Wall Clock")
    print("=" * 72)

    # ----------------------------------------------------------------- #
    rule("An expression, read on a zone's wall clock")

    weekday_open = CronSchedule("30 9 * * MON-FRI", NEW_YORK)
    print("  '30 9 * * MON-FRI' in New York, after Friday 2 October 10:00:")
    for line in firings(weekday_open, "2026-10-02T10:00", 3):
        print(f"    {line}")
    london = CronSchedule("30 9 * * MON-FRI", "Europe/London")
    print("  the same expression in London:")
    for line in firings(london, "2026-10-02T10:00", 1):
        print(f"    {line}")
    print()
    print("  The zone is part of the schedule and is required: an expression says")
    print("  nothing about whose 09:30 it means.")

    # ----------------------------------------------------------------- #
    rule("Day of month and day of week")

    print("  '0 9 1 * MON' -- the first of the month, and every Monday:")
    for line in firings(CronSchedule("0 9 1 * MON", NEW_YORK), "2026-06-26T12:00", 3):
        print(f"    {line}")
    print("  '0 12 */2 * SUN' -- a stepped '*' leaves the day of month unrestricted:")
    for line in firings(CronSchedule("0 12 */2 * SUN", NEW_YORK), "2026-06-01T00:00", 2):
        print(f"    {line}")
    print()
    print("  When both day fields are restricted, a day matches if either does; when")
    print("  one begins with '*', only the other applies -- Vixie cron's rule, which")
    print("  most implementations copy, and the one stated in alphalab.scheduler.cron.")

    # ----------------------------------------------------------------- #
    rule("Spring forward: 8 March, 02:00 jumps to 03:00")

    print("  '30 2 * * *' -- 02:30 does not exist on the 8th:")
    for line in firings(CronSchedule("30 2 * * *", NEW_YORK), "2026-03-06T12:00", 2):
        print(f"    {line}")
    print("  '*/30 * * * *' across the jump:")
    for line in firings(CronSchedule("*/30 * * * *", NEW_YORK), "2026-03-08T00:45", 4):
        print(f"    {line}")
    print()
    print("  A minute that does not exist that day does not fire. The half-hourly")
    print("  schedule loses 02:00 and 02:30 from its wall clock, and its UTC column")
    print("  shows it still fired every thirty minutes of elapsed time.")

    # ----------------------------------------------------------------- #
    rule("Fall back: 1 November, 02:00 returns to 01:00")

    print("  '*/30 * * * *' across the repeated hour:")
    for line in firings(CronSchedule("*/30 * * * *", NEW_YORK), "2026-11-01T00:15", 5):
        print(f"    {line}")
    print("  '30 1 * * *' -- 01:30 occurs twice on the 1st:")
    for line in firings(CronSchedule("30 1 * * *", NEW_YORK), "2026-10-31T12:00", 2):
        print(f"    {line}")
    print()
    print("  A minute that occurs twice fires once, at its first occurrence, so the")
    print("  half-hourly schedule is silent through the hour's second pass -- ninety")
    print("  minutes of elapsed time between 05:30 and 07:00 UTC. A schedule that must")
    print("  fire every thirty minutes of elapsed time is an INTERVAL timer, which")
    print("  counts seconds and has no wall clock to read.")

    # ----------------------------------------------------------------- #
    rule("Refused when written")

    for expression, zone in (
        ("0 0 31 4 *", NEW_YORK),
        ("5/15 * * * *", NEW_YORK),
        ("@daily", NEW_YORK),
        ("0 9 L * *", NEW_YORK),
        ("0 9 * * *", "Mars/Olympus"),
    ):
        try:
            CronSchedule(expression, zone)
            print("  NOT REFUSED")
        except SchedulerValidationError as error:
            refused(f"{expression!r} in {zone}", error)
    print()
    print("  The thirty-first of April never comes, so the schedule is refused when")
    print("  it is written rather than becoming a timer that waits forever.")

    # ----------------------------------------------------------------- #
    rule("On the engine: fired, then rescheduled")

    start = at("2026-10-02T16:00")
    state = SchedulerEngine.initialize(start)
    state = SchedulerEngine.schedule_timer(
        state, cron_timer("OPEN-CHECK", weekday_open, after=start), start
    )
    for _ in range(5):
        upcoming = next_timer(state)
        if upcoming is None:
            break
        state, fired = SchedulerEngine.advance_clock(state, upcoming.target_timestamp)
        print(
            f"  fired {', '.join(event.timer_id for event in fired)} at "
            f"{shown(upcoming.target_timestamp)}"
        )
    after_the_week = state.timers["OPEN-CHECK"].target_timestamp
    print(f"  next: {shown(after_the_week)}")

    jump = at("2026-10-15T12:00")
    passed, probe = 0, after_the_week
    while probe <= jump:
        passed += 1
        following = weekday_open.next_after(probe)
        if following is None:
            break
        probe = following
    state, fired = SchedulerEngine.advance_clock(state, jump)
    rescheduled = state.timers["OPEN-CHECK"].target_timestamp
    print()
    print(f"  the clock jumps to {shown(jump)}, past {passed} of its instants:")
    print(f"    fired {len(fired)} time, rescheduled to {shown(rescheduled)}")
    print()
    print("  Each advance fires a due timer once and reschedules it to the schedule's")
    print("  next instant after the new time; the instants jumped past are not")
    print("  replayed. A caller that must see each one advances to each in turn, as")
    print("  the loop above does with next_timer.")

    # ----------------------------------------------------------------- #
    rule("A cron timer that does not follow its schedule")

    for label, timer in (
        ("a CRON timer naming no schedule", Timer("BARE", jump + 60.0, ScheduleType.CRON)),
        (
            "a target its schedule does not fire at",
            Timer("OFF", at("2026-10-16T09:31"), ScheduleType.CRON, cron=weekday_open),
        ),
    ):
        try:
            SchedulerEngine.schedule_timer(state, timer, jump)
            print("  NOT REFUSED")
        except SchedulerValidationError as error:
            refused(label, error)


if __name__ == "__main__":
    main()
