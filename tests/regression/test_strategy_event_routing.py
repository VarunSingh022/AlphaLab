"""The strategy dispatcher identifies a market event exactly.

Until v2.16 ``alphalab.strategy.dispatcher`` selected the market hooks with
``type(event).__name__ == "TickReceived"`` and three siblings. A bare class name
is not a type. Three packages here define a ``TickReceived``, a
``QuoteReceived`` or a ``TradeReceived``, and the comparison matched all of them:
``alphalab.live.events.TickReceived`` carries ``provider_id`` / ``symbol`` /
``tick_type`` where the canonical event carries a ``tick``, so it was routed to
``on_tick``, the strategy read ``event.tick``, and the ``AttributeError`` was
converted into a ``FAILED`` strategy -- blamed for a routing mistake the
framework made.

The fix is not ``isinstance``. ADR-0016 decision 3 is normative --
"``alphalab.strategy`` acquires no dependency on ``alphalab.instrument`` or
``alphalab.market``" -- and ``test_instrument_identity_reaches_a_fill.py``
enforces it, so the canonical types cannot be imported into that package. A
module *and* a name identify a class exactly; a name alone does not. That is the
whole change.

Because the dispatcher cannot use the types, this file checks it against the
layer that can: :mod:`alphalab.runtime.execution_pipeline` may import both
vocabularies and does use ``isinstance``. Every canonical market event is
asserted class by class, and the two mechanisms are asserted to agree, so the
name table cannot drift away from the types it stands for.

It also pins the two depth events as a *stated* non-route rather than an
accident of the list.
"""

from collections.abc import Iterable
from dataclasses import fields
from decimal import Decimal
from typing import Any

import pytest

from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import (
    BarClosed,
    BookUpdated,
    MarketEvent,
    QuoteReceived,
    SnapshotCreated,
    TickReceived,
    TradeReceived,
)
from alphalab.market.level import OrderBookLevel
from alphalab.market.quote import Quote
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.tick import Tick
from alphalab.strategy.context import (
    NoMarket,
    NoOrders,
    NoPortfolio,
    NoRiskView,
    StrategyContext,
)
from alphalab.strategy.dispatcher import Dispatcher
from alphalab.strategy.events import FillEvent, Intent, OrderEvent, TimerEvent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.state import LifecycleState, StrategyState

ASSET = "ASSET-1"


class _Clock:
    def now(self) -> float:
        return 1.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def _context() -> StrategyContext:
    """A context that supplies nothing and says so. Routing does not read it."""

    return StrategyContext(
        portfolio=NoPortfolio(),
        market=NoMarket(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=NoRiskView(),
        config=None,
        orders=NoOrders(),
    )


class Recorder(BaseStrategy):
    """A strategy that records which hook it was called through."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def _hook(self, name: str, event: Any) -> Iterable[Intent]:
        self.calls.append((name, event))
        return ()

    def on_tick(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._hook("on_tick", event)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._hook("on_quote", event)

    def on_trade(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._hook("on_trade", event)

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._hook("on_bar", event)

    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        return self._hook("on_fill", event)

    def on_order(self, context: StrategyContext, event: OrderEvent) -> Iterable[Intent]:
        return self._hook("on_order", event)

    def on_timer(self, context: StrategyContext, event: TimerEvent) -> Iterable[Intent]:
        return self._hook("on_timer", event)


def _running(recorder: Recorder) -> StrategyState:
    return StrategyState(strategy_id="S1", instance=recorder, status=LifecycleState.RUNNING)


def _tick() -> Tick:
    return Tick(ASSET, 1.0, Decimal("100"), Decimal("1"), "TRD-1", "SIM", "USD")


def _quote() -> Quote:
    return Quote(
        ASSET, 1.0, Decimal("99"), Decimal("101"), Decimal("1"), Decimal("1"), "SIM", "USD"
    )


def _bar() -> Bar:
    return Bar(
        ASSET,
        1.0,
        Decimal("100"),
        Decimal("101"),
        Decimal("99"),
        Decimal("100"),
        Decimal("10"),
        Decimal("100"),
        1,
        TimeFrame.M1,
    )


def _snapshot() -> OrderBookSnapshot:
    level = OrderBookLevel(Decimal("100"), Decimal("1"), 1)
    return OrderBookSnapshot(ASSET, 1.0, (level,), (level,), 1)


#: Every canonical market event, and the hook it must reach (``None`` = no hook).
ROUTING: tuple[tuple[MarketEvent, str | None], ...] = (
    (TickReceived("e1", 1.0, _tick()), "on_tick"),
    (QuoteReceived("e2", 1.0, _quote()), "on_quote"),
    (TradeReceived("e3", 1.0, _tick()), "on_trade"),
    (BarClosed("e4", 1.0, _bar()), "on_bar"),
    (BookUpdated("e5", 1.0, _snapshot()), None),
    (SnapshotCreated("e6", 1.0, _snapshot()), None),
)


@pytest.mark.parametrize(("event", "hook"), ROUTING, ids=lambda v: getattr(v, "event_id", str(v)))
def test_every_canonical_market_event_routes_where_it_is_documented_to(
    event: MarketEvent, hook: str | None
) -> None:
    recorder = Recorder()
    state = _running(recorder)

    after, intents, lifecycle = Dispatcher.dispatch_event(state, event, _context(), 1.0)

    assert [name for name, _ in recorder.calls] == ([hook] if hook else [])
    assert after is state, "routing must not change the strategy state"
    assert intents == ()
    assert lifecycle == ()


def test_the_routing_table_covers_every_canonical_market_event() -> None:
    """A new market event must be a routing decision, not a silent omission."""

    import alphalab.market.events as market_events

    declared = {
        obj
        for name in dir(market_events)
        if isinstance(obj := getattr(market_events, name), type)
        and issubclass(obj, MarketEvent)
        and obj is not MarketEvent
    }
    covered = {type(event) for event, _ in ROUTING}
    assert declared == covered, (
        "alphalab.market.events gained or lost an event; decide where it routes "
        "in alphalab.strategy.dispatcher and record it here."
    )


def test_a_foreign_event_sharing_a_canonical_name_is_not_routed() -> None:
    """The exact misroute the name comparison produced, from the live package."""

    from alphalab.live.events import TickReceived as LiveTickReceived

    canonical: type = TickReceived
    assert LiveTickReceived is not canonical
    assert LiveTickReceived.__name__ == canonical.__name__
    # It is a different shape: no `tick`, which is what a strategy would read.
    assert "tick" not in {f.name for f in fields(LiveTickReceived)}

    foreign = LiveTickReceived("e1", 1.0, "PROVIDER", ASSET, "LAST")
    recorder = Recorder()
    state = _running(recorder)

    after, intents, lifecycle = Dispatcher.dispatch_event(state, foreign, _context(), 1.0)

    assert recorder.calls == [], "a foreign class must not reach a market hook"
    assert after.status is LifecycleState.RUNNING, "and must not fail the strategy"
    assert intents == ()
    assert lifecycle == ()


def test_the_marketdata_events_that_collide_by_name_are_not_routed_either() -> None:
    """`marketdata` is the second package whose event names collide."""

    from alphalab.marketdata.events import QuoteReceived as WireQuoteReceived
    from alphalab.marketdata.events import TradeReceived as WireTradeReceived

    canonical_quote: type = QuoteReceived
    canonical_trade: type = TradeReceived
    assert WireQuoteReceived is not canonical_quote
    assert WireTradeReceived is not canonical_trade

    recorder = Recorder()
    state = _running(recorder)
    for foreign in (
        WireQuoteReceived("e1", 1.0, "PROVIDER", ASSET),
        WireTradeReceived("e2", 1.0, "PROVIDER", ASSET),
    ):
        after, _, _ = Dispatcher.dispatch_event(state, foreign, _context(), 1.0)
        assert after.status is LifecycleState.RUNNING

    assert recorder.calls == []


def test_the_strategy_events_still_route_by_type() -> None:
    """The three branches that were already `isinstance` are unchanged."""

    recorder = Recorder()
    state = _running(recorder)
    events: tuple[Any, ...] = (
        FillEvent("f1", 1.0, "ORD-1", ASSET, Decimal("1"), Decimal("100")),
        OrderEvent("o1", 1.0, "ORD-1", ASSET, "ACCEPTED", ""),
        TimerEvent("t1", 1.0, "TIMER-1", "INTERVAL"),
    )
    for event in events:
        Dispatcher.dispatch_event(state, event, _context(), 1.0)

    assert [name for name, _ in recorder.calls] == ["on_fill", "on_order", "on_timer"]


def test_an_unroutable_object_leaves_the_strategy_untouched() -> None:
    """Unchanged behaviour, now reached by type rather than by a missing name."""

    recorder = Recorder()
    state = _running(recorder)

    after, intents, lifecycle = Dispatcher.dispatch_event(state, object(), _context(), 1.0)

    assert recorder.calls == []
    assert after is state
    assert intents == ()
    assert lifecycle == ()


def test_routing_agrees_with_the_canonical_types_class_by_class() -> None:
    """The name table stands for types. Here it is checked against them.

    ``market_hook_for`` may not import ``alphalab.market``; this test may. Every
    event the canonical vocabulary declares is resolved both ways and the two
    answers must match, so a rename, a move or a new event cannot leave the
    table quietly pointing at nothing.
    """

    import alphalab.market.events as market_events
    from alphalab.strategy.dispatcher import market_hook_for

    by_type = {
        TickReceived: "on_tick",
        QuoteReceived: "on_quote",
        TradeReceived: "on_trade",
        BarClosed: "on_bar",
        BookUpdated: None,
        SnapshotCreated: None,
    }
    for event, expected in ROUTING:
        assert market_hook_for(event) == expected
        assert by_type[type(event)] == expected

    assert all(
        cls.__module__ == dispatcher_module
        for cls, dispatcher_module in ((cls, "alphalab.market.events") for cls in by_type)
    ), "the canonical events moved; update MARKET_EVENTS_MODULE"
    assert market_events.TickReceived is TickReceived


def test_the_module_is_part_of_the_match() -> None:
    """A bare name match is what misrouted the live event. Pin the module too."""

    from alphalab.live.events import TickReceived as LiveTickReceived
    from alphalab.strategy.dispatcher import MARKET_EVENT_HOOKS, market_hook_for

    assert LiveTickReceived.__name__ in MARKET_EVENT_HOOKS
    assert market_hook_for(LiveTickReceived("e1", 1.0, "P", ASSET, "LAST")) is None
