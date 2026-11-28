"""GR-1: three venues in three zones from a pack; settlement per venue; a missing entry refused.

``examples/data/packs/synthetic_venues_2025.json`` is a synthetic pack, clearly
labelled: New York, London and Tokyo's 2025 sessions, holidays and settlement
cycles, as the example declares them. Read from it, by hand:

* **Three zones.** New York trades 09:30-16:00, London 08:00-16:30, Tokyo
  09:00-11:30 and 12:30-15:30, each in its own zone: Tokyo is shut at noon.
* **Settlement per venue, by each venue's calendar.** A purchase on Friday 29
  August in New York settles T+1 on Tuesday 2 September, Labor Day between. One
  on Thursday 21 August in London settles T+2 on Tuesday 26 August, the summer
  bank holiday between. One on Thursday 11 September in Tokyo settles T+2 on
  Tuesday 16 September, Respect for the Aged Day between. Each settles at its
  venue's next open.
* **What was announced later is not in it.** The pack was taken on 31 December
  2024, and holds New York's closure of 9 January, announced the day before.
* **A missing entry is refused.** A venue the pack states no calendar or
  settlement cycle for; a day outside its range, inside a run as well; and a
  pack whose entries are not the ones its digest states.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from alphalab.backtesting import BacktestEngine, Funding, MarketDataset, RunSchedule
from alphalab.core.enums import AssetType
from alphalab.data.exceptions import DataValidationError
from alphalab.data.packs import calendar_from_pack, load_pack, pack_from_primitives
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.reference import settlement_from_pack
from alphalab.portfolio.fx import FxRate
from alphalab.portfolio.settlement import SETTLES_AT
from alphalab.runtime.calendars import VenueCalendars
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    backtest_config,
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
)

ROOT = Path(__file__).resolve().parents[2]
PACK_FILE = ROOT / "examples" / "data" / "packs" / "synthetic_venues_2025.json"
PACK = load_pack(PACK_FILE)
D = Decimal
ZONES = {"XNYS": "America/New_York", "XLON": "Europe/London", "XTKS": "Asia/Tokyo"}
CALENDARS = {venue: calendar_from_pack(PACK, venue) for venue in ZONES}


def local(venue: str, day: str, at: str) -> float:
    return datetime.fromisoformat(f"{day}T{at}").replace(tzinfo=ZoneInfo(ZONES[venue])).timestamp()


RECORDS = {
    "XNYS": InstrumentRecord(
        "ACME", AssetType.EQUITY, "XNYS", "USD", settlement=settlement_from_pack(PACK, "XNYS")
    ),
    "XLON": InstrumentRecord(
        "BRIT", AssetType.EQUITY, "XLON", "GBP", settlement=settlement_from_pack(PACK, "XLON")
    ),
    "XTKS": InstrumentRecord(
        "NIPP", AssetType.EQUITY, "XTKS", "JPY", settlement=settlement_from_pack(PACK, "XTKS")
    ),
}
#: When each purchase is made, local time, and when it settles: the next open
#: after the venue's settlement cycle, counted over its calendar.
TRADES = {
    "XNYS": (local("XNYS", "2025-08-29", "15:00"), local("XNYS", "2025-09-02", "09:30")),
    "XLON": (local("XLON", "2025-08-21", "15:00"), local("XLON", "2025-08-26", "08:00")),
    "XTKS": (local("XTKS", "2025-09-11", "14:00"), local("XTKS", "2025-09-16", "09:00")),
}


class Buyer(BaseStrategy):
    """Buys ten of each venue's share at its trade instant; stateless."""

    def __init__(self, plan: dict[tuple[float, str], Decimal]) -> None:
        self._plan = plan

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        wanted = self._plan.get((event.quote.timestamp, event.quote.asset_id))
        if wanted is None:
            return ()
        return (Intent("S", event.quote.asset_id, wanted, timestamp=event.quote.timestamp),)


def run(trades: dict[str, tuple[float, float]] = TRADES) -> Any:
    base = backtest_config("S", starting_cash=D("1000000"))
    pipeline = replace(
        base.pipeline,
        currency="USD",
        also_settles=frozenset({"GBP", "JPY"}),
        budget=base.pipeline.budget.in_currency("USD"),
        instruments=registry_of(*RECORDS.values()),
        calendars=VenueCalendars(CALENDARS),
    )
    plan = {(at, RECORDS[venue].asset_id): D("10") for venue, (at, _) in trades.items()}
    quotes = [quote(RECORDS[venue].asset_id, at, D("100")) for venue, (at, _) in trades.items()]
    return BacktestEngine.run(
        replace(base, pipeline=pipeline),
        MarketDataset.of("GR1", sorted(quotes, key=lambda q: q.timestamp)),
        running_strategy_state("S", Buyer(plan)),
        context_factory,
        schedule=RunSchedule.of(
            rates=[
                FxRate("GBP", "USD", D("1.27"), 1.0, "test"),
                FxRate("JPY", "USD", D("0.0068"), 1.0, "test"),
            ],
            fundings=[Funding(D("10000"), "GBP", 1.5), Funding(D("2000000"), "JPY", 1.5)],
        ),
    )


def test_three_venues_keep_three_zones_and_their_own_sessions() -> None:
    assert {venue: calendar.timezone_name for venue, calendar in CALENDARS.items()} == ZONES
    tokyo = CALENDARS["XTKS"]
    assert not tokyo.is_open(local("XTKS", "2025-09-11", "12:00"))  # lunch
    assert tokyo.is_open(local("XTKS", "2025-09-11", "13:00"))
    assert CALENDARS["XLON"].is_open(local("XLON", "2025-09-11", "08:00"))
    assert not CALENDARS["XNYS"].is_open(local("XNYS", "2025-09-11", "09:00"))
    # Each covers 2025 whole, in its own zone.
    assert {calendar.coverage for calendar in CALENDARS.values()} == {
        (date(2025, 1, 1), date(2026, 1, 1))
    }


def test_each_venue_settles_by_its_own_cycle_and_calendar() -> None:
    assert {
        venue: (record.settlement.offset_days if record.settlement else None)
        for venue, record in RECORDS.items()
    } == {
        "XNYS": 1,
        "XLON": 2,
        "XTKS": 2,
    }
    result = run()
    settled = {
        t.asset_id: t.metadata[SETTLES_AT]
        for t in result.state.portfolio.ledger.transactions
        if SETTLES_AT in t.metadata
    }
    for venue, (_, settles) in TRADES.items():
        assert settled[RECORDS[venue].asset_id] == settles


def test_what_was_announced_after_the_pack_was_taken_is_not_in_it() -> None:
    assert not CALENDARS["XNYS"].is_trading_day(date(2025, 1, 9))  # announced 30 December
    assert PACK.as_of == datetime(2024, 12, 31, tzinfo=ZoneInfo("UTC")).timestamp()


def test_a_missing_entry_is_refused() -> None:
    with pytest.raises(DataValidationError, match="states no calendar 'XPAR'"):
        calendar_from_pack(PACK, "XPAR")
    with pytest.raises(DataValidationError, match="states no settlement cycle for 'XPAR'"):
        settlement_from_pack(PACK, "XPAR")
    with pytest.raises(DataValidationError, match="is stated for"):
        CALENDARS["XLON"].is_trading_day(date(2026, 1, 2))
    # Inside a run: a purchase in New York on 5 January 2026, outside the pack.
    late = {"XNYS": (local("XNYS", "2026-01-05", "15:00"), 0.0)}
    with pytest.raises(DataValidationError, match="is stated for"):
        run(late)


def test_a_pack_whose_entries_are_not_the_ones_its_digest_states_is_refused() -> None:
    payload = json.loads(PACK_FILE.read_text(encoding="utf-8"))
    payload["entries"] = [
        entry for entry in payload["entries"] if entry.get("date") != "2025-08-25"
    ]
    with pytest.raises(DataValidationError, match="are not the entries it states"):
        pack_from_primitives(payload)
