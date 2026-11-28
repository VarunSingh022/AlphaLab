"""A venue's calendar and settlement cycle, read from a reference pack (v4.3, ADR-0059, M10).

* **Written and read.** A pack reads back equal through its JSON form and from
  a file, and a file that is not a pack -- not JSON, a non-finite number, a
  field too many -- is refused.
* **A calendar from a pack** holds the pack's range as its coverage, the local
  dates it holds whole, and refuses a day outside it. An instant on its first
  day is answered without asking the day before, which it does not state, when
  no window runs overnight. Coverage travels with the calendar's persisted
  form, and a calendar without one writes what it always wrote.
* **Refused**, each with the reason: two zones for one calendar; a schedule
  that changes within the range; a weekday, time or date that does not read;
  a holiday outside the range; a settlement cycle that changes, or whose basis
  or days do not read.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from alphalab.conventions.settlement import SettlementBasis, SettlementRule
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.data.exceptions import DataValidationError
from alphalab.data.packs import (
    HOLIDAY,
    SETTLEMENT_CYCLE,
    VENUE_SESSION,
    PackSource,
    ReferencePack,
    calendar_from_pack,
    load_pack,
    pack_from_primitives,
    pack_to_primitives,
    reference_pack_digest,
)
from alphalab.instrument.reference import settlement_from_pack
from alphalab.runtime.calendars import VenueCalendars, venue_calendars_from_primitives


def at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


FROM, TO = at("2025-01-01T00:00:00"), at("2025-02-01T00:00:00")


def session(
    weekday: str = "0", opens: str = "09:00", closes: str = "17:00", **kw: Any
) -> dict[str, Any]:
    entry = {
        "type": VENUE_SESSION,
        "calendar_id": "XTST",
        "timezone": "UTC",
        "weekday": weekday,
        "opens": opens,
        "closes": closes,
        "effective_at": FROM,
        "announced_at": FROM,
    }
    return {**entry, **kw}


def holiday(day: str = "2025-01-06", **kw: Any) -> dict[str, Any]:
    entry = {
        "type": HOLIDAY,
        "calendar_id": "XTST",
        "date": day,
        "name": "a holiday",
        "effective_at": FROM,
        "announced_at": FROM,
    }
    return {**entry, **kw}


def cycle(basis: str = "TRADING_DAYS", days: str = "2", **kw: Any) -> dict[str, Any]:
    entry = {
        "type": SETTLEMENT_CYCLE,
        "venue": "XTST",
        "basis": basis,
        "days": days,
        "effective_at": FROM,
        "announced_at": FROM,
    }
    return {**entry, **kw}


def pack(*entries: dict[str, Any]) -> ReferencePack:
    held = tuple(entries)
    return ReferencePack(
        "test/venues", "1", PackSource("a test", "this file"), FROM, FROM, TO, "test only",
        held, reference_pack_digest(held),
    )  # fmt: skip


WEEK = tuple(session(str(day)) for day in range(5))
PACK = pack(*WEEK, holiday(), cycle())


def test_a_pack_reads_back_equal_through_json_and_from_a_file(tmp_path: Path) -> None:
    text = json.dumps(pack_to_primitives(PACK))
    assert pack_from_primitives(json.loads(text)) == PACK
    where = tmp_path / "venues.json"
    where.write_text(text, encoding="utf-8")
    assert load_pack(where) == PACK


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("not json", "is not a pack"),
        ('{"pack_id": NaN}', "is not a pack"),
        ("[]", "has exactly the fields"),
    ],
    ids=["text", "nan", "shape"],
)
def test_a_file_that_is_not_a_pack_is_refused(tmp_path: Path, text: str, reason: str) -> None:
    where = tmp_path / "bad.json"
    where.write_text(text, encoding="utf-8")
    with pytest.raises(DataValidationError, match=reason):
        load_pack(where)
    extra = {**pack_to_primitives(PACK), "extra": 1}
    with pytest.raises(DataValidationError, match="has exactly the fields"):
        pack_from_primitives(extra)


def test_a_calendar_from_a_pack_holds_its_sessions_holidays_and_coverage() -> None:
    calendar = calendar_from_pack(PACK, "XTST")
    assert calendar.coverage == (date(2025, 1, 1), date(2025, 2, 1))
    assert calendar.weekly_sessions[0] == (SessionWindow(time(9), time(17)),)
    assert not calendar.is_trading_day(date(2025, 1, 6))  # the holiday, a Monday
    assert calendar.is_trading_day(date(2025, 1, 7))
    # Its first day is answered without the day before, which it does not state.
    assert calendar.trading_day_of(at("2025-01-01T10:00:00")) == date(2025, 1, 1)
    with pytest.raises(DataValidationError, match="is stated for"):
        calendar.is_trading_day(date(2025, 2, 3))
    assert settlement_from_pack(PACK, "XTST") == SettlementRule(SettlementBasis.TRADING_DAYS, 2)


def test_coverage_travels_with_the_calendar_and_a_calendar_without_one_is_unchanged() -> None:
    covered = calendar_from_pack(PACK, "XTST")
    plain = MarketCalendar("XOLD", "UTC", {0: (SessionWindow(time(9), time(17)),)})
    venues = VenueCalendars({"XTST": covered, "XOLD": plain})
    payload = json.loads(json.dumps(venues.__serializable__()))
    assert "coverage" not in payload["by_exchange"]["XOLD"]
    assert payload["by_exchange"]["XTST"]["coverage"] == ["2025-01-01", "2025-02-01"]
    assert venue_calendars_from_primitives(payload, "calendars") == venues


def test_an_overnight_calendar_does_not_assume_the_day_before_its_coverage() -> None:
    night = pack(session("0", "18:00", "06:00"), session("1", "18:00", "06:00"))
    calendar = calendar_from_pack(night, "XTST")
    with pytest.raises(DataValidationError, match="is stated for"):
        calendar.is_open(at("2025-01-01T03:00:00"))


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (
            lambda: calendar_from_pack(
                pack(session(), session("1", timezone="Asia/Tokyo")), "XTST"
            ),
            "one zone",
        ),
        (
            lambda: calendar_from_pack(
                pack(session(), session("1", effective_at=FROM + 86400)), "XTST"
            ),
            "changes within its range",
        ),
        (lambda: calendar_from_pack(pack(session("7")), "XTST"), "weekdays are 0"),
        (lambda: calendar_from_pack(pack(session(opens="9am")), "XTST"), "not an ISO time"),
        (lambda: calendar_from_pack(pack(session(), holiday("6 Jan")), "XTST"), "not an ISO date"),
        (
            lambda: calendar_from_pack(pack(session(), holiday("2025-03-03")), "XTST"),
            "outside its range",
        ),
        (
            lambda: settlement_from_pack(pack(cycle(), cycle(effective_at=FROM + 86400)), "XTST"),
            "changes within its range",
        ),
        (lambda: settlement_from_pack(pack(cycle("T_PLUS")), "XTST"), "a basis is one of"),
        (lambda: settlement_from_pack(pack(cycle(days="-1")), "XTST"), "a basis is one of"),
    ],
    ids=[
        "zones",
        "schedule-changes",
        "weekday",
        "time",
        "date",
        "holiday-outside",
        "cycle-changes",
        "basis",
        "days",
    ],
)
def test_what_a_pack_cannot_state_of_a_venue_is_refused(
    build: Callable[[], object], reason: str
) -> None:
    with pytest.raises(DataValidationError, match=reason):
        build()
