"""Subscriptions route events (EXE-007), and ``on_start`` is delivered once (EXE-005).

Until v3.11 a strategy's subscriptions were recorded and never read -- every
running strategy received every event for every asset -- and ``on_start`` was
declared on the protocol and never called.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import BarClosed, QuoteReceived
from alphalab.market.quote import Quote
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.engine import StrategyEngine
from alphalab.strategy.events import (
    FillEvent,
    Intent,
    LifecycleTransitioned,
    SliceClosed,
    TimerEvent,
)
from alphalab.strategy.exceptions import StrategyValidationError
from alphalab.strategy.protocol import BaseStrategy, SliceStrategyProtocol, StrategyProtocol
from alphalab.strategy.state import LifecycleState, RuntimeState, StrategyState
from alphalab.strategy.subscription import (
    SUBSCRIBE_ALL,
    Subscriptions,
    Topic,
    market_topic,
    topic_of,
)
from tests.integration.harness import context_factory

A = str(uuid.UUID(int=0xA))
B = str(uuid.UUID(int=0xB))


def _quote_event(asset_id: str, at: float = 1.0) -> QuoteReceived:
    quote = Quote(
        asset_id, at, Decimal("99"), Decimal("101"), Decimal("1"), Decimal("1"), "X", "USD"
    )
    return QuoteReceived(f"Q-{asset_id[-2:]}-{at}", at, quote)


def _bar_event(asset_id: str, at: float = 1.0) -> BarClosed:
    bar = Bar(
        asset_id=asset_id,
        timestamp=at,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
        timeframe=TimeFrame.M1,
        vwap=None,
        trade_count=None,
    )
    return BarClosed(f"B-{asset_id[-2:]}-{at}", at, bar)


class _Recorder(BaseStrategy):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def on_start(self, context: StrategyContext) -> None:
        self.calls.append("start")

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.calls.append(f"quote:{event.quote.asset_id}")
        return ()

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.calls.append(f"bar:{event.bar.asset_id}")
        return ()

    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        self.calls.append("fill")
        return ()

    def on_slice(self, context: StrategyContext, event: SliceClosed) -> Iterable[Intent]:
        self.calls.append("slice")
        return ()


def _runtime(*entries: tuple[str, StrategyProtocol, frozenset[str]]) -> RuntimeState:
    return RuntimeState(
        strategies={
            strategy_id: StrategyState(
                strategy_id, LifecycleState.RUNNING, instance, subscriptions=subscriptions
            )
            for strategy_id, instance, subscriptions in entries
        }
    )


class _CountingFactory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, strategy_id: str) -> StrategyContext:
        self.calls.append(strategy_id)
        return context_factory(strategy_id)


# --------------------------------------------------------------------------- #
# The grammar
# --------------------------------------------------------------------------- #


def test_the_grammar_reads_everything_a_topic_and_a_scoped_topic() -> None:
    parsed = Subscriptions.parse({"quotes", f"bars:{A}"})

    assert not parsed.everything
    assert parsed.topics == frozenset({Topic.QUOTES})
    assert parsed.scoped == frozenset({(Topic.BARS, A)})
    assert parsed.accepts(Topic.QUOTES, B)
    assert parsed.accepts(Topic.BARS, A)
    assert not parsed.accepts(Topic.BARS, B)
    assert not parsed.accepts(Topic.FILLS)
    assert Subscriptions.parse({SUBSCRIBE_ALL}).accepts(Topic.SLICES)


@pytest.mark.parametrize(
    ("declared", "message"),
    [
        (set(), "subscribed to nothing"),
        ({"quote"}, "names no topic"),
        ({"market_data"}, "names no topic"),
        ({f"fills:{A}"}, "only market topics"),
        ({"bars: "}, "blank asset"),
        ({7}, "is a string"),
    ],
)
def test_a_declaration_outside_the_grammar_is_refused(declared: set[Any], message: str) -> None:
    with pytest.raises(StrategyValidationError, match=message):
        Subscriptions.parse(declared)


def test_a_strategy_that_declared_nothing_receives_everything() -> None:
    state = StrategyState("S", LifecycleState.RUNNING, BaseStrategy())

    assert state.subscriptions == frozenset({SUBSCRIBE_ALL})
    assert state.routing.everything


def test_an_invalid_declaration_is_refused_when_the_state_is_built() -> None:
    with pytest.raises(StrategyValidationError, match="names no topic"):
        StrategyState("S", LifecycleState.RUNNING, BaseStrategy(), subscriptions=frozenset({"x"}))


def test_topics_are_exact_about_the_canonical_vocabulary() -> None:
    assert market_topic(_quote_event(A)) == (Topic.QUOTES, A)
    assert market_topic(_bar_event(B)) == (Topic.BARS, B)
    assert topic_of(FillEvent("F", 1.0, "O", A, Decimal("1"), Decimal("1"))) == (Topic.FILLS, None)
    assert topic_of(TimerEvent("T", 1.0, "t", "every")) == (Topic.TIMERS, None)
    assert topic_of(SliceClosed("S", 1.0, (A,))) == (Topic.SLICES, None)

    class QuoteReceived:  # a host application's look-alike, same name, other module
        quote = _quote_event(A).quote

    assert topic_of(QuoteReceived()) is None
    assert topic_of(object()) is None


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def test_a_strategy_receives_only_what_it_subscribed_to() -> None:
    bars, quotes_of_a, everything = _Recorder(), _Recorder(), _Recorder()
    state = _runtime(
        ("BARS", bars, frozenset({"bars"})),
        ("QUOTES-A", quotes_of_a, frozenset({f"quotes:{A}"})),
        ("ALL", everything, frozenset({SUBSCRIBE_ALL})),
    )
    factory = _CountingFactory()

    for event in (_quote_event(A), _quote_event(B), _bar_event(B)):
        state, _ = StrategyEngine.process_event(state, event, factory, event.timestamp)

    assert bars.calls == ["start", f"bar:{B}"]
    assert quotes_of_a.calls == ["start", f"quote:{A}"]
    assert everything.calls == ["start", f"quote:{A}", f"quote:{B}", f"bar:{B}"]
    # A context is built only for a strategy the event reaches.
    assert factory.calls == ["QUOTES-A", "ALL", "ALL", "BARS", "ALL"]


def test_an_event_nobody_subscribed_to_changes_nothing() -> None:
    state = _runtime(("BARS", _Recorder(), frozenset({"bars"})))
    factory = _CountingFactory()

    after, intents = StrategyEngine.process_event(state, _quote_event(A), factory, 1.0)

    assert after is state and intents == ()
    assert factory.calls == []


def test_the_slice_hook_is_optional() -> None:
    """A strategy written against the ten hooks satisfies the protocol and is not called."""

    class TenHooks:
        def __init__(self) -> None:
            self.base = BaseStrategy()

        def __getattr__(self, name: str) -> Any:
            if name == "on_slice":
                raise AttributeError(name)
            return getattr(self.base, name)

    ten = TenHooks()
    assert not isinstance(ten, SliceStrategyProtocol)
    state = _runtime(("TEN", ten, frozenset({SUBSCRIBE_ALL})))

    after, intents = StrategyEngine.process_event(
        state, SliceClosed("S", 1.0, (A,)), context_factory, 1.0
    )

    assert intents == ()
    assert after.strategies["TEN"].status is LifecycleState.RUNNING


# --------------------------------------------------------------------------- #
# on_start
# --------------------------------------------------------------------------- #


def test_on_start_is_delivered_once_before_the_first_event_with_its_context() -> None:
    seen: list[tuple[str, object]] = []

    class Starter(_Recorder):
        def on_start(self, context: StrategyContext) -> None:
            seen.append(("start", context))

        def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
            seen.append(("quote", context))
            return ()

    state = _runtime(("S", Starter(), frozenset({"quotes"})))
    state, _ = StrategyEngine.process_event(state, _quote_event(A, 1.0), context_factory, 1.0)
    state, _ = StrategyEngine.process_event(state, _quote_event(A, 2.0), context_factory, 2.0)

    assert [kind for kind, _ in seen] == ["start", "quote", "quote"]
    assert seen[0][1] is seen[1][1], "on_start sees the context of the event that follows it"
    assert state.strategies["S"].started


def test_on_start_waits_for_the_first_event_the_strategy_receives() -> None:
    recorder = _Recorder()
    state = _runtime(("BARS", recorder, frozenset({"bars"})))

    state, _ = StrategyEngine.process_event(state, _quote_event(A), context_factory, 1.0)
    assert recorder.calls == [] and not state.strategies["BARS"].started

    state, _ = StrategyEngine.process_event(state, _bar_event(A, 2.0), context_factory, 2.0)
    assert recorder.calls == ["start", f"bar:{A}"]


def test_a_raising_on_start_fails_the_strategy_before_the_event_reaches_it() -> None:
    class Broken(_Recorder):
        def on_start(self, context: StrategyContext) -> None:
            raise RuntimeError("no warmup data")

    broken = Broken()
    state = _runtime(("S", broken, frozenset({SUBSCRIBE_ALL})))
    state, intents = StrategyEngine.process_event(state, _quote_event(A), context_factory, 1.0)

    failed = state.strategies["S"]
    assert failed.status is LifecycleState.FAILED
    assert "on_start" in (failed.last_error or "") and "no warmup data" in (failed.last_error or "")
    assert broken.calls == [] and intents == ()
    assert [
        event.new_state for event in state.events if isinstance(event, LifecycleTransitioned)
    ] == ["FAILED"]
    assert len(state.events) == 1


def test_a_started_strategy_is_not_started_again() -> None:
    recorder = _Recorder()
    state = _runtime(("S", recorder, frozenset({SUBSCRIBE_ALL})))
    state = replace(state, strategies={"S": replace(state.strategies["S"], started=True)})

    StrategyEngine.process_event(state, _quote_event(A), context_factory, 1.0)

    assert recorder.calls == [f"quote:{A}"]
