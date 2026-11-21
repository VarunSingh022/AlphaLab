"""NEXT_OPEN: an order decided at one close trades at the next open (ADR-0060, GAP-18).

Every figure here is calculated by hand from the bars below.

* **Daily bars.** Decided at the first bar's close of 100, a market order fills
  at the second bar's open of 103, a gap included. ``NEXT_EVENT`` fills it at
  that bar's close of 105, and ``SAME_EVENT`` at 100. The fill is recorded at
  the instant of the bar that delivered the open, as an ``OPG`` fill is.
* **One-minute bars across a session boundary.** Decided at a session's last
  bar, the order fills at the next session's first bar's open of 101 when the
  venue's calendar is declared, the post- and pre-market bars passing it by.
  Without a calendar it fills at the next bar's open of 99, whatever its hour.
* **What NEXT_OPEN leaves as NEXT_EVENT.** A held limit order's resting turn
  over the next bar, and a quote's turn, which has no open.
* **What is recorded.** The timing in ``execution_assumptions``, not counted
  optimistic, and a snapshot at version 9 that resumes to the same fill.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from alphalab.common.order_terms import MARKET, OrderTerms, OrderType, TimeInForce
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.execution.policy import FillTiming, ImmediateFill
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import BarClosed
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.calendars import VenueCalendars
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineResult,
    ExecutionPipelineState,
)
from alphalab.runtime.snapshot import RuntimeObjects, capture, from_primitives, restore
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = str(UUID(int=0x0E1))
ASSET = str(UUID(int=0x0E2, version=4))
ZONE = ZoneInfo("America/New_York")


class _BarPlan(BaseStrategy):
    """Places the planned order at the bars stamped in ``plan``."""

    def __init__(self, plan: Mapping[float, Decimal], terms: OrderTerms | None = None) -> None:
        self._plan = dict(plan)
        self._terms = terms

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        delta = self._plan.get(event.bar.timestamp)
        if delta is None:
            return ()
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=ASSET,
                target=delta,
                timestamp=event.bar.timestamp,
                terms=MARKET if self._terms is None else self._terms,
            ),
        )


def _pipeline(
    timing: FillTiming,
    plan: Mapping[float, Decimal],
    *,
    calendars: VenueCalendars | None = None,
    terms: OrderTerms | None = None,
) -> ExecutionPipelineState:
    config = replace(pipeline_config(STRATEGY_ID), fill_timing=timing)
    if calendars is not None:
        config = replace(config, calendars=calendars)
    strategy = _BarPlan(plan, terms)
    return ExecutionPipeline.initialize(config, running_strategy_state(STRATEGY_ID, strategy), 1.0)


def _bar(
    at: float,
    open_: str,
    close: str,
    frame: TimeFrame,
    low: str = "",
    high: str = "",
    asset: str = ASSET,
) -> Any:
    o, c = Decimal(open_), Decimal(close)
    lo = Decimal(low) if low else min(o, c)
    hi = Decimal(high) if high else max(o, c)
    bar = Bar(asset, at, o, hi, lo, c, Decimal("1000000"), None, None, frame)
    return BarClosed(event_id=f"B-{asset[-3:]}-{at}", timestamp=at, bar=bar)


def _step(state: ExecutionPipelineState, event: Any) -> ExecutionPipelineResult:
    return ExecutionPipeline.process_market_event(state, event, context_factory)


def _run(state: ExecutionPipelineState, events: Iterable[Any]) -> list[tuple[Decimal, float]]:
    fills: list[tuple[Decimal, float]] = []
    for event in events:
        result = _step(state, event)
        fills.extend((fill.price, fill.filled_at) for fill in result.fills)
        state = result.state
    return fills


# --------------------------------------------------------------------------- #
# Daily bars: the next open, a gap included
# --------------------------------------------------------------------------- #

D1 = 1_767_657_600.0  # a daily bar's stamp; the next is a day later
D2 = D1 + 86_400.0
DAILY = (
    _bar(D1, "98", "100", TimeFrame.D1),
    _bar(D2, "103", "105", TimeFrame.D1, low="102", high="106"),
)


@pytest.mark.parametrize(
    ("timing", "expected"),
    [
        (FillTiming.SAME_EVENT, [(Decimal("100"), D1)]),
        (FillTiming.NEXT_EVENT, [(Decimal("105"), D2)]),
        (FillTiming.NEXT_OPEN, [(Decimal("103"), D2)]),
    ],
    ids=["same-event", "next-event", "next-open"],
)
def test_a_daily_order_fills_at_the_timings_price(
    timing: FillTiming, expected: list[tuple[Decimal, float]]
) -> None:
    assert _run(_pipeline(timing, {D1: Decimal("10")}), DAILY) == expected


def test_the_book_pays_the_open_and_the_next_event_marks_it_at_the_close() -> None:
    """A fill priced at the open values its position there until any next event re-marks it.

    The convention every fill priced away from its event's mark keeps -- a resting
    limit's, an ``OPG`` auction's -- since the book re-marks lazily (PRF-001).
    """

    state = _pipeline(FillTiming.NEXT_OPEN, {D1: Decimal("10")})
    state = _step(state, DAILY[0]).state
    currency = state.config.currency
    before = state.portfolio.cash.balance(currency)
    result = _step(state, DAILY[1])

    position = result.state.portfolio.positions[ASSET]
    assert position.quantity == Decimal("10")
    spent = before - result.state.portfolio.cash.balance(currency)
    assert spent == Decimal("1030")  # 10 at the open of 103
    assert dict(result.state.allocation.reservations) == {}
    assert result.state.market_prices[ASSET] == Decimal("105")
    assert position.market_price == Decimal("103")  # its fill, pending a mark
    other = str(UUID(int=0x0E3, version=4))
    later = _step(result.state, _bar(D2 + 1.0, "50", "50", TimeFrame.D1, asset=other))
    assert later.state.portfolio.positions[ASSET].market_price == Decimal("105")


# --------------------------------------------------------------------------- #
# One-minute bars across a session boundary
# --------------------------------------------------------------------------- #

WEEKDAYS = {day: (SessionWindow(time(9, 30), time(16, 0)),) for day in range(5)}
CALENDAR = MarketCalendar("XTST", "America/New_York", WEEKDAYS)


def _local(day: date, hour: int, minute: int) -> float:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZONE).timestamp()


MONDAY, TUESDAY = date(2026, 1, 5), date(2026, 1, 6)
LAST = _local(MONDAY, 16, 0)  # 15:59-16:00, the session's last bar
AFTER = _local(MONDAY, 16, 1)  # 16:00-16:01, post-market
EARLY = _local(TUESDAY, 9, 1)  # 09:00-09:01, pre-market
FIRST = _local(TUESDAY, 9, 31)  # 09:30-09:31, the next session's first bar
MINUTES = (
    _bar(LAST, "100", "100", TimeFrame.M1),
    _bar(AFTER, "99", "98", TimeFrame.M1),
    _bar(EARLY, "97", "97", TimeFrame.M1),
    _bar(FIRST, "101", "102", TimeFrame.M1),
)


def test_with_the_venues_calendar_the_order_fills_at_the_next_sessions_first_open() -> None:
    state = _pipeline(
        FillTiming.NEXT_OPEN, {LAST: Decimal("10")}, calendars=VenueCalendars(default=CALENDAR)
    )
    assert _run(state, MINUTES) == [(Decimal("101"), FIRST)]


def test_without_a_calendar_every_bar_is_in_session() -> None:
    assert _run(_pipeline(FillTiming.NEXT_OPEN, {LAST: Decimal("10")}), MINUTES) == [
        (Decimal("99"), AFTER)
    ]


def test_the_session_rule_is_next_opens_alone() -> None:
    """NEXT_EVENT keeps filling at the next bar's close, calendar or not."""

    state = _pipeline(
        FillTiming.NEXT_EVENT, {LAST: Decimal("10")}, calendars=VenueCalendars(default=CALENDAR)
    )
    assert _run(state, MINUTES) == [(Decimal("98"), AFTER)]


def test_an_out_of_session_bar_still_expires_what_is_due() -> None:
    """A day limit order decided in session expires at the close, not at the next session."""

    terms = OrderTerms(order_type=OrderType.LIMIT, limit_price=Decimal("90"))
    state = _pipeline(
        FillTiming.NEXT_OPEN,
        {_local(MONDAY, 15, 59): Decimal("10")},
        calendars=VenueCalendars(default=CALENDAR),
        terms=terms,
    )
    decided = _step(state, _bar(_local(MONDAY, 15, 59), "100", "100", TimeFrame.M1))
    (order,) = decided.oms_orders
    assert order.expire_at == _local(MONDAY, 16, 0)
    passed = _step(decided.state, MINUTES[1])  # the post-market bar
    assert passed.fills == ()
    (expired,) = passed.oms_orders
    assert expired.status.name == "EXPIRED"
    assert not passed.state.oms.active_orders


# --------------------------------------------------------------------------- #
# What NEXT_OPEN leaves as NEXT_EVENT
# --------------------------------------------------------------------------- #


def test_a_limit_orders_resting_turn_is_next_events() -> None:
    """A held limit order meets the next bar from its open under both: the better open, 103."""

    terms = OrderTerms(
        order_type=OrderType.LIMIT, limit_price=Decimal("104"), time_in_force=TimeInForce.GTC
    )
    fills = {
        timing: _run(_pipeline(timing, {D1: Decimal("10")}, terms=terms), DAILY)
        for timing in (FillTiming.NEXT_EVENT, FillTiming.NEXT_OPEN)
    }
    assert fills[FillTiming.NEXT_OPEN] == fills[FillTiming.NEXT_EVENT] == [(Decimal("103"), D2)]


def test_a_quote_has_no_open_so_its_turn_is_next_events() -> None:
    config = replace(pipeline_config(STRATEGY_ID), fill_timing=FillTiming.NEXT_OPEN)
    strategy = ScriptedStrategy(STRATEGY_ID, ASSET, {2.0: Decimal("10")})
    state = ExecutionPipeline.initialize(config, running_strategy_state(STRATEGY_ID, strategy), 1.0)

    def quote(state: ExecutionPipelineState, at: float, mid: str) -> ExecutionPipelineResult:
        return ExecutionPipeline.process_quote(
            state, sized_quote(ASSET, at, Decimal(mid), Decimal("1000")), context_factory
        )

    decided = quote(state, 2.0, "100")
    assert decided.fills == ()
    (fill,) = quote(decided.state, 3.0, "105").fills
    assert (fill.price, fill.filled_at) == (Decimal("105"), 3.0)


# --------------------------------------------------------------------------- #
# What is recorded
# --------------------------------------------------------------------------- #


def test_the_timing_is_described_and_not_counted_optimistic() -> None:
    config = replace(pipeline_config(STRATEGY_ID), fill_timing=FillTiming.NEXT_OPEN)
    assumptions = execution_assumptions(config, ImmediateFill())

    assert assumptions.fill_timing is FillTiming.NEXT_OPEN
    assert assumptions.settings()["execution.fill_timing"] == "next_open"
    assert not any("SAME_EVENT" in sentence for sentence in assumptions.optimistic)


def test_a_run_paused_with_an_order_working_resumes_to_the_same_fill() -> None:
    state = _pipeline(FillTiming.NEXT_OPEN, {D1: Decimal("10")})
    decided = _step(state, DAILY[0]).state
    payload = deserialize(serialize(capture(decided)))
    assert payload["schema_version"] == 9

    strategy = decided.strategy.strategies[STRATEGY_ID].instance
    objects = RuntimeObjects(
        sizing_model=decided.config.sizing_model,
        simulator=decided.config.simulator,
        strategies={STRATEGY_ID: strategy},
        instruments=decided.config.instruments,
    )
    resumed = restore(from_primitives(payload), objects)
    assert _run(resumed, DAILY[1:]) == _run(decided, DAILY[1:]) == [(Decimal("103"), D2)]
