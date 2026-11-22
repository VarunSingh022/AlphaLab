"""When a rebalancing strategy rebalances: its triggers (ADR-0063 section 6).

* **Calendar.** The first slice of each day, week or month fires, read in a
  stated zone -- one instant is two months apart in UTC and in Tokyo -- or by a
  declared calendar's sessions, a real :class:`MarketCalendar` through the
  protocol: an overnight session's slice belongs to the day it opened, and a
  slice outside every session fires nothing.
* **Threshold and drift** measure from the last target, every asset either
  holds counted, and fire before there is one.
* **Refusals**: no zone is assumed, and every distance is stated and positive.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from types import MappingProxyType

import pytest

from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.rebalancing import (
    CalendarTrigger,
    DriftTrigger,
    Period,
    RebalanceMoment,
    RebalancingInputError,
    SessionCalendar,
    ThresholdTrigger,
    Trigger,
)


def at(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()


def moment(
    instant: float, weights: dict[str, float] | None = None, target: dict[str, float] | None = None
) -> RebalanceMoment:
    return RebalanceMoment(
        instant,
        MappingProxyType(weights or {}),
        None if target is None else MappingProxyType(target),
    )


def fired(trigger: Trigger, instants: list[float]) -> list[bool]:
    memory: str | None = None
    out = []
    for instant in instants:
        fires, memory = trigger.fires(moment(instant), memory)
        out.append(fires)
    return out


# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #


def test_a_monthly_trigger_fires_at_each_months_first_slice() -> None:
    trigger = CalendarTrigger(Period.MONTH, zone="UTC")
    instants = [at(2025, 1, 30), at(2025, 1, 31), at(2025, 2, 3), at(2025, 2, 4), at(2025, 3, 1)]
    assert fired(trigger, instants) == [True, False, True, False, True]


def test_a_weekly_trigger_reads_the_iso_week() -> None:
    # 2025-01-05 is a Sunday, 2025-01-06 a Monday: two ISO weeks. The year
    # boundary inside one ISO week (2024-12-30 to 2025-01-05) is not a new week.
    trigger = CalendarTrigger(Period.WEEK, zone="UTC")
    instants = [at(2024, 12, 30), at(2025, 1, 2), at(2025, 1, 5), at(2025, 1, 6)]
    assert fired(trigger, instants) == [True, False, False, True]


def test_a_daily_trigger_fires_once_a_day() -> None:
    trigger = CalendarTrigger(Period.DAY, zone="UTC")
    instants = [at(2025, 1, 2, 9), at(2025, 1, 2, 16), at(2025, 1, 3, 9)]
    assert fired(trigger, instants) == [True, False, True]


def test_the_zone_decides_the_date() -> None:
    # 2025-01-31 16:00 UTC is 2025-02-01 01:00 in Tokyo: a new month there, not in UTC.
    instants = [at(2025, 1, 31, 10), at(2025, 1, 31, 16)]
    assert fired(CalendarTrigger(Period.MONTH, zone="UTC"), instants) == [True, False]
    assert fired(CalendarTrigger(Period.MONTH, zone="Asia/Tokyo"), instants) == [True, True]


def overnight() -> MarketCalendar:
    """A market open 17:00 to 16:00 the next day, Sunday to Thursday evenings, in Chicago."""

    window = (SessionWindow(time(17, 0), time(16, 0)),)
    return MarketCalendar("OVERNIGHT", "America/Chicago", dict.fromkeys((6, 0, 1, 2, 3), window))


def test_a_calendar_is_taken_by_protocol_and_read_by_its_sessions() -> None:
    calendar = overnight()
    assert isinstance(calendar, SessionCalendar)
    trigger = CalendarTrigger(Period.DAY, calendar=calendar)
    # 2025-01-06 23:30 UTC is 17:30 Monday in Chicago: Monday evening's session.
    # 2025-01-07 05:00 UTC is 23:00 Monday: the same session, the same day.
    # 2025-01-07 22:30 UTC is 16:30 Tuesday: shut -- no period, nothing fires.
    # 2025-01-07 23:30 UTC is 17:30 Tuesday: Tuesday evening's session.
    instants = [
        at(2025, 1, 6, 23, 30),
        at(2025, 1, 7, 5),
        at(2025, 1, 7, 22, 30),
        at(2025, 1, 7, 23, 30),
    ]
    assert calendar.trading_day_of(instants[1]) == date(2025, 1, 6)
    assert calendar.trading_day_of(instants[2]) is None
    assert fired(trigger, instants) == [True, False, False, True]


def test_a_slice_outside_every_session_keeps_the_memory() -> None:
    trigger = CalendarTrigger(Period.DAY, calendar=overnight())
    fires, memory = trigger.fires(moment(at(2025, 1, 7, 22, 30)), "2025-01-06")
    assert (fires, memory) == (False, "2025-01-06")


def test_the_rendering_names_the_period_and_where_it_is_read() -> None:
    assert CalendarTrigger(Period.WEEK, zone="Europe/London").rendering() == [
        "trigger=calendar",
        "period=week",
        "zone='Europe/London'",
    ]
    assert CalendarTrigger(Period.DAY, calendar=overnight()).rendering()[-1] == (
        "calendar='OVERNIGHT'"
    )


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: CalendarTrigger(Period.MONTH), "state exactly one"),
        (
            lambda: CalendarTrigger(Period.MONTH, zone="UTC", calendar=overnight()),
            "state exactly one",
        ),
        (lambda: CalendarTrigger(Period.MONTH, zone="Mars/Olympus"), "not an IANA zone"),
        (lambda: CalendarTrigger("month", zone="UTC"), "must be a Period"),  # type: ignore[arg-type]
        (lambda: CalendarTrigger(Period.DAY, calendar=object()), "not a session calendar"),  # type: ignore[arg-type]
    ],
    ids=["neither", "both", "zone", "period", "calendar"],
)
def test_a_calendar_trigger_states_its_period_and_where(
    build: Callable[[], object], reason: str
) -> None:
    with pytest.raises(RebalancingInputError, match=reason):
        build()


# --------------------------------------------------------------------------- #
# Threshold and drift
# --------------------------------------------------------------------------- #


def test_before_a_first_target_threshold_and_drift_fire() -> None:
    for trigger in (ThresholdTrigger(0.05), DriftTrigger(0.1)):
        assert trigger.fires(moment(0.0, {"A": 0.5}), None) == (True, None)


def test_a_threshold_fires_on_one_weight_beyond_its_distance() -> None:
    trigger = ThresholdTrigger(0.05)
    target = {"A": 0.5, "B": 0.5}
    assert trigger.fires(moment(0.0, {"A": 0.54, "B": 0.46}, target), None)[0] is False
    assert trigger.fires(moment(0.0, {"A": 0.56, "B": 0.44}, target), None)[0] is True
    # An asset the book holds and the target does not is a distance too: C's
    # 0.06 alone fires, with A and B within their distance.
    assert trigger.fires(moment(0.0, {"A": 0.5, "B": 0.47, "C": 0.06}, target), None)[0] is True
    assert trigger.fires(moment(0.0, {"A": 0.5, "B": 0.47, "C": 0.03}, target), None)[0] is False


def test_drift_fires_on_the_books_total_distance() -> None:
    trigger = DriftTrigger(0.1)
    target = {"A": 0.4, "B": 0.3, "C": 0.3}
    # 0.04 + 0.04 + 0.00 = 0.08 <= 0.1, then 0.06 + 0.06 = 0.12 > 0.1.
    assert trigger.fires(moment(0.0, {"A": 0.44, "B": 0.26, "C": 0.3}, target), None)[0] is False
    assert trigger.fires(moment(0.0, {"A": 0.46, "B": 0.24, "C": 0.3}, target), None)[0] is True
    # A threshold at 0.05 would fire on the second and not the first.
    assert (
        ThresholdTrigger(0.05).fires(moment(0.0, {"A": 0.44, "B": 0.26, "C": 0.3}, target), None)[0]
        is False
    )


@pytest.mark.parametrize("value", [0.0, -0.1, float("inf"), float("nan"), True, "0.1"])
def test_a_distance_is_stated_positive_and_finite(value: object) -> None:
    with pytest.raises(RebalancingInputError):
        ThresholdTrigger(value)  # type: ignore[arg-type]
    with pytest.raises(RebalancingInputError):
        DriftTrigger(value)  # type: ignore[arg-type]


def test_every_trigger_satisfies_the_protocol() -> None:
    for trigger in (
        CalendarTrigger(Period.MONTH, zone="UTC"),
        ThresholdTrigger(0.05),
        DriftTrigger(0.1),
    ):
        assert isinstance(trigger, Trigger)
