"""Latency pricing: no order meets the market before its latency has passed (ADR-0060).

Every figure here is calculated by hand from the bars below.

* **One-minute bars, 90 seconds of latency.** Decided at the close of the bar
  stamped ``T``, a market order is ready at ``T+90``. Under ``STAMP`` it fills
  where its fill timing says -- the deciding close of 100, or the next bar's
  close of 101 or open of 99 -- stamped 90 seconds later. Under
  ``POST_LATENCY_EVENT`` the ``T+60`` bar passes it by, and its first turn is
  the ``T+120`` bar's, stamped there: it reached the market at ``T+90``, within
  that bar, so it meets the bar's close of 102 alone -- not its open of 103,
  under ``NEXT_OPEN`` either, which was before it was there.
* **A latency shorter than the bar.** Ready at ``T+30``, within the next bar:
  its close of 101, never its open of 99, which is the price at the decision.
* **No latency.** Every timing trades as it does under ``STAMP``.
* **Daily bars.** A session is met from its open: 103 under ``NEXT_OPEN``.
* **A limit order arriving within a bar** sees its close alone: a low before it
  reached the market does not fill it, and the next bar's range does.
* **On its way.** No turn and no queue place; a day order whose session closes
  meanwhile expires; an order that reaches the market after a halt has ended
  did not wait for the reopening auction.
* **What is recorded.** The pricing in the assumptions and in a version-9
  snapshot, which resumes to the same fill.
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

from alphalab.backtesting import BacktestEngine, PriceTimeQueue, RunSchedule
from alphalab.common.order_terms import MARKET, OrderTerms, OrderType, TimeInForce
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.data.feed import TradeAggressor
from alphalab.execution import LatencyPricing
from alphalab.execution.latency import ConstantLatency, DeterministicLatency, LatencyModel
from alphalab.execution.policy import FillTiming, ImmediateFill
from alphalab.execution.queue import QueuePosition
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument import TradingControls
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import BarClosed
from alphalab.market.quote import Quote
from alphalab.market.tick import Tick
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.assumptions import describe, execution_assumptions
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
from tests.integration import test_computed_auctions as auctions
from tests.integration import test_queue_model as queues
from tests.integration import test_reopening_auctions as reopening
from tests.integration import test_trading_controls as controls
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

D = Decimal
STAMP = LatencyPricing.STAMP
POST = LatencyPricing.POST_LATENCY_EVENT
STRATEGY_ID = str(UUID(int=0x111))
ASSET = str(UUID(int=0x112, version=4))
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
    pricing: LatencyPricing,
    latency: LatencyModel,
    timing: FillTiming,
    plan: Mapping[float, Decimal],
    *,
    terms: OrderTerms | None = None,
    calendars: VenueCalendars | None = None,
) -> ExecutionPipelineState:
    config = replace(
        pipeline_config(STRATEGY_ID, simulator=ExecutionSimulator(latency_model=latency)),
        fill_timing=timing,
        latency_pricing=pricing,
    )
    if calendars is not None:
        config = replace(config, calendars=calendars)
    strategy = _BarPlan(plan, terms)
    return ExecutionPipeline.initialize(config, running_strategy_state(STRATEGY_ID, strategy), 1.0)


def _bar(
    at: float, open_: str, close: str, frame: TimeFrame, low: str = "", high: str = ""
) -> BarClosed:
    o, c = D(open_), D(close)
    lo = D(low) if low else min(o, c)
    hi = D(high) if high else max(o, c)
    bar = Bar(ASSET, at, o, hi, lo, c, D("1000000"), None, None, frame)
    return BarClosed(event_id=f"B-{at}", timestamp=at, bar=bar)


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
# One-minute bars
# --------------------------------------------------------------------------- #

T = 1_767_657_600.0  # a minute's end
MINUTES = (
    _bar(T, "100", "100", TimeFrame.M1),  # decides
    _bar(T + 60, "99", "101", TimeFrame.M1, low="98"),  # T to T+60
    _bar(T + 120, "103", "102", TimeFrame.M1, low="100", high="104"),  # T+60 to T+120
    _bar(T + 180, "104", "105", TimeFrame.M1, low="101", high="106"),  # T+120 to T+180
)
NINETY = ConstantLatency(90.0)


@pytest.mark.parametrize(
    ("pricing", "timing", "expected"),
    [
        (STAMP, FillTiming.SAME_EVENT, (D("100"), T + 90)),
        (STAMP, FillTiming.NEXT_EVENT, (D("101"), T + 150)),
        (STAMP, FillTiming.NEXT_OPEN, (D("99"), T + 150)),
        (POST, FillTiming.SAME_EVENT, (D("102"), T + 120)),
        (POST, FillTiming.NEXT_EVENT, (D("102"), T + 120)),
        (POST, FillTiming.NEXT_OPEN, (D("102"), T + 120)),
    ],
    ids=[
        "stamp-same-event",
        "stamp-next-event",
        "stamp-next-open",
        "post-same-event",
        "post-next-event",
        "post-next-open",
    ],
)
def test_a_latency_longer_than_the_bar_fills_at_a_later_bars_price(
    pricing: LatencyPricing, timing: FillTiming, expected: tuple[Decimal, float]
) -> None:
    assert _run(_pipeline(pricing, NINETY, timing, {T: D("10")}), MINUTES) == [expected]


def test_an_order_on_its_way_has_no_turn_and_records_when_it_arrives() -> None:
    state = _pipeline(POST, NINETY, FillTiming.SAME_EVENT, {T: D("10")})
    decided = _step(state, MINUTES[0])
    assert decided.fills == ()
    (order,) = decided.oms_orders
    assert order.metadata["latency_ready_at"] == repr(T + 90)
    passed = _step(decided.state, MINUTES[1])
    assert passed.fills == ()
    (working_id,) = passed.state.oms.active_orders
    working = passed.state.oms.orders.find(working_id)
    assert working.status.name == "ACCEPTED"
    assert working.filled_quantity == D("0")
    assert "latency_first_turn_at" not in working.metadata


@pytest.mark.parametrize("timing", list(FillTiming), ids=lambda timing: timing.value)
def test_a_latency_shorter_than_the_bar_still_misses_the_open(timing: FillTiming) -> None:
    """Ready at T+30, inside the next bar: its close of 101, stamped at the bar."""

    state = _pipeline(POST, ConstantLatency(30.0), timing, {T: D("10")})
    assert _run(state, MINUTES) == [(D("101"), T + 60)]


@pytest.mark.parametrize("timing", list(FillTiming), ids=lambda timing: timing.value)
def test_with_no_latency_every_timing_trades_as_it_does_under_stamp(timing: FillTiming) -> None:
    fills = {
        pricing: _run(_pipeline(pricing, ConstantLatency(0.0), timing, {T: D("10")}), MINUTES)
        for pricing in LatencyPricing
    }
    assert fills[POST] == fills[STAMP]
    assert (
        fills[STAMP]
        == {
            FillTiming.SAME_EVENT: [(D("100"), T)],
            FillTiming.NEXT_EVENT: [(D("101"), T + 60)],
            FillTiming.NEXT_OPEN: [(D("99"), T + 60)],
        }[timing]
    )


def test_a_drawn_latency_is_the_models_draw_for_the_order() -> None:
    model = DeterministicLatency(0.0, 120.0)
    state = _pipeline(POST, model, FillTiming.NEXT_EVENT, {T: D("10")})
    decided = _step(state, MINUTES[0])
    (order,) = decided.oms_orders
    draw = model.calculate(str(order.order_id.value), T)
    assert 0 < draw < 120
    assert order.metadata["latency_ready_at"] == repr(T + draw)
    # Ready within the T+60 bar or the T+120 one: the close of the bar it arrived in.
    expected = (D("101"), T + 60) if draw <= 60 else (D("102"), T + 120)
    assert _run(decided.state, MINUTES[1:]) == [expected]


# --------------------------------------------------------------------------- #
# Daily bars: a session is met from its open
# --------------------------------------------------------------------------- #

D1 = 1_767_657_600.0
D2 = D1 + 86_400.0
DAILY = (
    _bar(D1, "98", "100", TimeFrame.D1),
    _bar(D2, "103", "105", TimeFrame.D1, low="102", high="106"),
)


@pytest.mark.parametrize(
    ("pricing", "timing", "expected"),
    [
        (POST, FillTiming.NEXT_OPEN, (D("103"), D2)),
        (POST, FillTiming.NEXT_EVENT, (D("105"), D2)),
        (POST, FillTiming.SAME_EVENT, (D("105"), D2)),
        (STAMP, FillTiming.NEXT_OPEN, (D("103"), D2 + 0.5)),
    ],
    ids=["post-next-open", "post-next-event", "post-same-event", "stamp-next-open"],
)
def test_a_daily_session_is_met_from_its_open(
    pricing: LatencyPricing, timing: FillTiming, expected: tuple[Decimal, float]
) -> None:
    state = _pipeline(pricing, ConstantLatency(0.5), timing, {D1: D("10")})
    assert _run(state, DAILY) == [expected]


# --------------------------------------------------------------------------- #
# Limit orders and expiry
# --------------------------------------------------------------------------- #

GTC_BID = OrderTerms(
    order_type=OrderType.LIMIT, limit_price=D("101.5"), time_in_force=TimeInForce.GTC
)


def test_a_limit_arriving_within_a_bar_sees_its_close_alone() -> None:
    """The T+60 bar passes it by; the T+120 bar's low of 100 may predate it; T+180's fills it."""

    state = _pipeline(POST, NINETY, FillTiming.NEXT_EVENT, {T: D("10")}, terms=GTC_BID)
    assert _run(state, MINUTES) == [(D("101.5"), T + 180)]
    # Stamped only, it rests over the whole next bar and takes its better open.
    stamped = _pipeline(STAMP, NINETY, FillTiming.NEXT_EVENT, {T: D("10")}, terms=GTC_BID)
    assert _run(stamped, MINUTES) == [(D("99"), T + 150)]


def test_a_marketable_limit_arriving_within_a_bar_takes_its_close() -> None:
    bid = replace(GTC_BID, limit_price=D("102.5"))
    state = _pipeline(POST, NINETY, FillTiming.NEXT_EVENT, {T: D("10")}, terms=bid)
    assert _run(state, MINUTES) == [(D("102"), T + 120)]


WEEKDAYS = {day: (SessionWindow(time(9, 30), time(16, 0)),) for day in range(5)}
CALENDAR = MarketCalendar("XTST", "America/New_York", WEEKDAYS)


def _local(day: date, hour: int, minute: int) -> float:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZONE).timestamp()


def test_a_day_order_whose_session_closes_on_its_way_expires_unfilled() -> None:
    monday = date(2026, 1, 5)
    decided_at, close = _local(monday, 15, 59), _local(monday, 16, 0)
    terms = OrderTerms(order_type=OrderType.LIMIT, limit_price=D("110"))
    state = _pipeline(
        POST,
        NINETY,
        FillTiming.NEXT_EVENT,
        {decided_at: D("10")},
        terms=terms,
        calendars=VenueCalendars(default=CALENDAR),
    )
    decided = _step(state, _bar(decided_at, "100", "100", TimeFrame.M1))
    (order,) = decided.oms_orders
    assert order.expire_at == close
    closed = _step(decided.state, _bar(close, "100", "100", TimeFrame.M1))
    assert closed.fills == ()
    (expired,) = closed.oms_orders
    assert expired.status.name == "EXPIRED"
    assert not closed.state.oms.active_orders


# --------------------------------------------------------------------------- #
# Quotes, queues and the reopening auction
# --------------------------------------------------------------------------- #


def test_at_quotes_the_first_quote_after_the_latency_trades() -> None:
    def fills(pricing: LatencyPricing) -> list[tuple[Decimal, float]]:
        config = replace(
            pipeline_config(
                STRATEGY_ID, simulator=ExecutionSimulator(latency_model=ConstantLatency(0.5))
            ),
            latency_pricing=pricing,
        )
        strategy = ScriptedStrategy(STRATEGY_ID, ASSET, {2.0: D("10")})
        state = ExecutionPipeline.initialize(
            config, running_strategy_state(STRATEGY_ID, strategy), 1.0
        )
        found: list[tuple[Decimal, float]] = []
        for at, mid in ((2.0, "100"), (2.3, "101"), (2.6, "103")):
            result = ExecutionPipeline.process_quote(
                state, sized_quote(ASSET, at, D(mid), D("1000")), context_factory
            )
            found.extend((fill.price, fill.filled_at) for fill in result.fills)
            state = result.state
        return found

    assert fills(STAMP) == [(D("100"), 2.5)]
    assert fills(POST) == [(D("103"), 2.6)]


def _queued_run(
    pricing: LatencyPricing, records: tuple[Quote | Tick | Bar, ...], latency: float = 1.5
) -> Any:
    base = queues.config(simulator=ExecutionSimulator(latency_model=ConstantLatency(latency)))
    config = replace(base, pipeline=replace(base.pipeline, latency_pricing=pricing))
    return BacktestEngine.run(
        config,
        queues.dataset(records),
        queues.strategies(),
        context_factory,
        queue=PriceTimeQueue(),
    )


def test_an_order_on_its_way_takes_no_place_in_the_queue_at_the_event_it_was_placed_on() -> None:
    placed = (queues.DEPLETION[0],)
    stamped = _queued_run(STAMP, placed)
    (order,) = stamped.state.oms.orders.orders()
    assert stamped.queue.positions == {
        str(order.order_id.value): QueuePosition(queues.LIMIT, D(300))
    }
    assert _queued_run(POST, placed).queue.positions == {}


def test_an_order_reaching_the_market_late_starts_from_an_unknown_queue() -> None:
    # Stamped only, it joins behind the 300 shown and fills as the queue clears.
    assert queues.fills(_queued_run(STAMP, queues.DEPLETION)) == [
        (3.5, D("20"), queues.LIMIT, "MAKER"),
        (4.5, D("50"), queues.LIMIT, "MAKER"),
        (5.5, D("30"), queues.LIMIT, "MAKER"),
    ]
    # Reaching the market at 1.5, after the print at 1.0, it knows nothing of the
    # queue, and no print at its limit is counted for it. The quote at 4.0 bids
    # below its limit, so nothing is ahead of it, and its offer at the limit fills
    # all 100 there.
    assert queues.fills(_queued_run(POST, queues.DEPLETION)) == [
        (4.0, D("100"), queues.LIMIT, "MAKER")
    ]


SELLER = TradeAggressor.SELLER


def _reopening(
    latency: float,
    declared: TradingControls | None = None,
    script: dict[tuple[float, str], tuple[Intent, ...]] = reopening.IN_THE_HALT,
    series: dict[float, tuple[str, str]] = reopening.BARS_AFTER,
) -> Any:
    base = controls.config(
        reopening.ACME,
        controls=reopening.halt() if declared is None else declared,
        simulator=ExecutionSimulator(latency_model=ConstantLatency(latency)),
    )
    config = replace(
        base,
        pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT, latency_pricing=POST),
    )
    return BacktestEngine.run(
        config,
        reopening.bars(series),
        running_strategy_state("S", reopening.OnBars(script)),
        context_factory,
        schedule=RunSchedule.of(),
    )


def _reopening_run(latency: float) -> list[tuple[float, Decimal, Decimal]]:
    return controls.trades(_reopening(latency))


def test_an_order_ready_inside_the_halt_waits_for_the_reopening_auction() -> None:
    # Ready at 3.5, before the halt ends at 4.5: the market order and the limit at
    # 105 cross at the 5.0 bar's open of 103, as without latency. The limit at 100
    # rests on, in the market since 3.5, and the 6.0 bar trades through it.
    assert _reopening_run(0.5) == [
        (5.0, D("10.000000"), D("103.00")),
        (5.0, D("5.000000"), D("103.00")),
        (6.0, D("7.000000"), D("100")),
    ]


def test_an_order_ready_after_the_halt_did_not_wait_for_the_auction() -> None:
    # Ready at 5.0: the halt ended without it. Its turn is continuous trading's, in
    # the bar it arrived within -- the market order at the close of 106, which
    # neither limit reaches. Resting from then, the limits meet the 6.0 bar from
    # its open: 105 at the better open of 104, 100 at its limit.
    assert _reopening_run(2.0) == [
        (5.0, D("10.000000"), D("106.00")),
        (6.0, D("5.000000"), D("104")),
        (6.0, D("7.000000"), D("100")),
    ]


def test_an_order_still_on_its_way_at_the_reopening_takes_no_part() -> None:
    # Ready at 5.5: the reopening passes it by, and at the 6.0 bar it arrives
    # within, the close of 99 fills all three -- the limits as marketable.
    assert _reopening_run(2.5) == [
        (6.0, D("10.000000"), D("99.00")),
        (6.0, D("5.000000"), D("99.00")),
        (6.0, D("7.000000"), D("99.00")),
    ]


#: The computed-auction test's book: at 102 it and the run's market order of 10
#: and limit of 20 at 102 match 80, balanced.
PUBLISHED = auctions.book(
    bids=(("103", "50"), ("101", "30")), offers=(("100", "40"), ("102", "40"))
)
CROSSING: dict[tuple[float, str], tuple[Intent, ...]] = {
    (3.0, reopening.ACME.asset_id): (
        controls.buy(3.0, "10"),
        controls.buy(3.0, "20", terms=reopening.limit("102")),
    )
}


def test_only_an_order_that_reached_the_market_in_the_halt_is_in_the_computed_book() -> None:
    waited = _reopening(0.5, auctions.computed(PUBLISHED), CROSSING, reopening.BARS)
    assert controls.trades(waited) == [
        (5.0, D("10.000000"), D("102")),
        (5.0, D("20.000000"), D("102")),
    ]
    held = auctions.reopened(waited)
    assert (held.price, held.matched, held.imbalance) == (D("102"), D("80"), D("0"))
    # Ready at 5.0, after the halt: the book alone matches 50 at 102 and at 103,
    # with 30 more offered than bid at each, and the highest wins. The run's
    # market order trades on in continuous trading, at the close of 106 of the
    # bar it arrived within.
    late = _reopening(2.0, auctions.computed(PUBLISHED), CROSSING, reopening.BARS)
    held = auctions.reopened(late)
    assert (held.price, held.matched, held.imbalance) == (D("103"), D("50"), D("-30"))
    assert controls.trades(late) == [(5.0, D("10.000000"), D("106.00"))]


# --------------------------------------------------------------------------- #
# What is recorded
# --------------------------------------------------------------------------- #


def test_the_pricing_is_described_only_when_declared() -> None:
    stamped = replace(
        pipeline_config(STRATEGY_ID, simulator=ExecutionSimulator(latency_model=NINETY))
    )
    assumed = execution_assumptions(stamped, ImmediateFill())
    assert assumed.latency == describe(NINETY)
    posted = execution_assumptions(replace(stamped, latency_pricing=POST), ImmediateFill())
    assert posted.latency == f"{describe(NINETY)}; pricing=post_latency_event"
    assert posted.settings()["execution.latency"] == posted.latency


def test_a_run_paused_with_an_order_on_its_way_resumes_to_the_same_fill() -> None:
    state = _pipeline(POST, NINETY, FillTiming.NEXT_EVENT, {T: D("10")})
    decided = _step(state, MINUTES[0]).state
    payload = deserialize(serialize(capture(decided)))
    assert payload["schema_version"] == 9
    assert payload["config"]["latency_pricing"] == "post_latency_event"

    strategy = decided.strategy.strategies[STRATEGY_ID].instance
    objects = RuntimeObjects(
        sizing_model=decided.config.sizing_model,
        simulator=decided.config.simulator,
        strategies={STRATEGY_ID: strategy},
        instruments=decided.config.instruments,
    )
    resumed = restore(from_primitives(payload), objects)
    assert resumed.config.latency_pricing is POST
    assert _run(resumed, MINUTES[1:]) == _run(decided, MINUTES[1:]) == [(D("102"), T + 120)]


def test_a_stamped_run_writes_no_pricing_and_an_unknown_one_is_refused() -> None:
    state = _pipeline(STAMP, NINETY, FillTiming.NEXT_EVENT, {T: D("10")})
    payload = deserialize(serialize(capture(_step(state, MINUTES[0]).state)))
    assert payload["schema_version"] == 8
    assert "latency_pricing" not in payload["config"]

    posted = _pipeline(POST, NINETY, FillTiming.NEXT_EVENT, {T: D("10")})
    written = deserialize(serialize(capture(_step(posted, MINUTES[0]).state)))
    written["config"]["latency_pricing"] = "later"
    with pytest.raises(StateDecodeError, match=r"config\.latency_pricing must be one of"):
        from_primitives(written)
