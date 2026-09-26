"""Information events, and where each one falls in a venue's trading day.

An event is anchored on when it could first be *traded*, not when it happened:
after the close is the next session, before the open is this session's open, a
holiday waits for the next trading day, and a lunch break waits for the
afternoon. Asserted against real calendars in real zones.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.alt_data import (
    INFORMATION_EVENT_KEY_SCHEME,
    AltDataInputError,
    InformationEvent,
    PointInTimeError,
    SessionTiming,
    canonical_event_key,
    place_in_session,
    place_record,
    surprise,
)
from alphalab.common import PointInTimeStamp, VisibilityRule
from alphalab.data.calendar import MarketCalendar
from tests.unit.alt_data.pit_harness import NYSE, SOURCE, TSE, event, ny, tokyo

PUB = VisibilityRule.PUBLICATION

# --------------------------------------------------------------------------- #
# The event
# --------------------------------------------------------------------------- #


def test_an_event_carries_its_category_measurements_and_surprise() -> None:
    release = event(
        "earnings.release",
        "AAA",
        ny("2024-05-07 16:05"),
        ny("2024-05-07 16:06"),
        measurements={"actual": "1.52", "consensus": "1.45"},
    )

    assert release.category == "earnings"
    assert release.measurement("actual") == Decimal("1.52")
    assert surprise(release, "actual", "consensus") == Decimal("0.07")


def test_a_missing_measurement_is_refused_rather_than_read_as_zero() -> None:
    release = event("earnings.release", "AAA", 1.0, 2.0, measurements={"actual": "1"})

    with pytest.raises(AltDataInputError, match="not zero"):
        surprise(release, "actual", "consensus")


def test_an_event_is_immutable_and_its_mappings_are_read_only() -> None:
    release = event("macro.cpi", "US", 1.0, 2.0, measurements={"actual": "3.1"})

    with pytest.raises(FrozenInstanceError):
        release.subject = "EU"  # type: ignore[misc]
    with pytest.raises(TypeError):
        release.measurements["actual"] = Decimal("9")  # type: ignore[index]


def test_the_callers_mapping_cannot_reach_into_the_event() -> None:
    values = {"actual": Decimal("1")}
    release = InformationEvent(
        "macro.cpi", "US", PointInTimeStamp.declared(1.0, 2.0), SOURCE, values, {}, 0
    )
    values["actual"] = Decimal("999")

    assert release.measurements["actual"] == Decimal("1")


def test_the_canonical_event_rendering_is_pinned() -> None:
    release = InformationEvent(
        "earnings.release",
        "AAA",
        PointInTimeStamp.declared(10.0, 11.0),
        SOURCE,
        {"consensus": Decimal("1.45"), "actual": Decimal("1.52")},
        {"fiscal_period": "FY2024Q1"},
        0,
    )

    assert canonical_event_key(release) == "\n".join(
        [
            INFORMATION_EVENT_KEY_SCHEME,
            "event_type='earnings.release'",
            "subject='AAA'",
            "revision=0",
            "measurements",
            "'actual'='1.52'",
            "'consensus'='1.45'",
            "attributes",
            "'fiscal_period'='FY2024Q1'",
            "source='panel.test_feed'",
            "source_version='2024.1'",
            "observed_at=10.0",
            "available_at=11.0",
            "basis=DECLARED",
            "effective_at=None",
            "ingested_at=None",
            "rule=''",
        ]
    )


def test_a_correction_is_a_second_event_sharing_the_first_ones_vintage() -> None:
    first = event("news.article", "AAA", 10.0, 11.0)
    correction = event("news.article", "AAA", 10.0, 50.0, revision=1)

    assert first.vintage_key == correction.vintage_key
    assert first.record_id != correction.record_id


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"event_type": "Earnings"}, "identifier"),
        ({"subject": ""}, "cannot be empty"),
        ({"measurements": {"Actual": Decimal("1")}}, "identifier"),
        ({"measurements": {"actual": Decimal("Infinity")}}, "finite"),
        ({"attributes": {"headline": "two\nlines"}}, "line break"),
        ({"revision": -2}, "revision"),
    ],
)
def test_a_malformed_event_is_refused(change: dict[str, Any], match: str) -> None:
    release = event("earnings.release", "AAA", 1.0, 2.0)

    with pytest.raises(AltDataInputError, match=match):
        replace(release, **change)


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


def test_information_after_the_close_is_tradable_at_the_next_open() -> None:
    placement = place_in_session(ny("2024-05-07 16:05"), NYSE)

    assert placement.timing is SessionTiming.AFTER_CLOSE
    assert placement.tradable_at == ny("2024-05-08 09:30")
    assert placement.calendar_id == "XNYS"


def test_pre_market_information_is_tradable_at_the_same_days_open() -> None:
    placement = place_in_session(ny("2024-05-07 08:00"), NYSE)

    assert placement.timing is SessionTiming.BEFORE_OPEN
    assert placement.tradable_at == ny("2024-05-07 09:30")


def test_information_during_the_session_is_tradable_at_once() -> None:
    instant = ny("2024-05-07 11:15")
    placement = place_in_session(instant, NYSE)

    assert placement.timing is SessionTiming.IN_SESSION
    assert placement.tradable_at == instant


def test_the_close_itself_is_after_the_session() -> None:
    """A session is [open, close): at 16:00 the venue has closed."""

    placement = place_in_session(ny("2024-05-07 16:00"), NYSE)

    assert placement.timing is SessionTiming.AFTER_CLOSE


def test_a_friday_evening_waits_for_monday_and_a_holiday_for_the_next_day() -> None:
    friday = place_in_session(ny("2024-05-10 17:00"), NYSE)
    saturday = place_in_session(ny("2024-05-11 12:00"), NYSE)
    holiday = place_in_session(ny("2024-07-04 10:00"), NYSE)

    assert friday.tradable_at == ny("2024-05-13 09:30")
    assert saturday.timing is SessionTiming.NON_TRADING_DAY
    assert saturday.tradable_at == ny("2024-05-13 09:30")
    assert holiday.timing is SessionTiming.NON_TRADING_DAY
    assert holiday.tradable_at == ny("2024-07-05 09:30")


def test_a_lunch_break_waits_for_the_afternoon_session() -> None:
    placement = place_in_session(tokyo("2024-05-07 12:00"), TSE)

    assert placement.timing is SessionTiming.BETWEEN_SESSIONS
    assert placement.tradable_at == tokyo("2024-05-07 12:30")


def test_a_continuous_market_trades_information_the_instant_it_exists() -> None:
    crypto = MarketCalendar.continuous("CRYPTO", "UTC")
    instant = ny("2024-05-11 03:17")

    placement = place_in_session(instant, crypto)

    assert placement.timing is SessionTiming.IN_SESSION
    assert placement.tradable_at == instant


def test_a_record_is_placed_at_its_knowledge_instant_not_its_occurrence() -> None:
    """Announced in the session, but the feed delivered it after the close."""

    release = event("earnings.release", "AAA", ny("2024-05-07 15:00"), ny("2024-05-07 16:20"))

    placement = place_record(release, PUB, NYSE)

    assert placement.instant == ny("2024-05-07 16:20")
    assert placement.tradable_at == ny("2024-05-08 09:30")


def test_a_record_with_no_established_availability_cannot_be_placed() -> None:
    rumour = event("news.article", "AAA", ny("2024-05-07 12:00"), None)

    with pytest.raises(PointInTimeError, match="not a"):
        place_record(rumour, PUB, NYSE)


def test_a_calendar_that_never_trades_refuses_rather_than_hangs() -> None:
    closed = MarketCalendar("NEVER", "UTC", weekly_sessions={})

    with pytest.raises(PointInTimeError, match="never be traded"):
        place_in_session(1_700_000_000.0, closed)
