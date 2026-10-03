"""An event reaches the strategies its index names, in registration order (ledger PRF-010).

Until v3.12 every event asked every strategy whether it wanted the event, so
each strategy cost about a third of a microsecond on every record whatever it
had subscribed to (the 1,000-strategy stress run). The runtime now keeps a
routing index of its strategies' subscriptions. The oracle is the question it
replaced: the strategies :meth:`Subscriptions.accepts` accepts, asked one by one
in registration order -- the same strategies, in the same order, for every
topic and asset.
"""

from __future__ import annotations

import copy
import pickle
import random
import uuid
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import BarClosed
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.engine import StrategyEngine
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.state import LifecycleState, RuntimeState, StrategyState
from alphalab.strategy.subscription import RoutingIndex, Subscriptions, Topic
from tests.integration.harness import context_factory

ASSETS = [str(uuid.UUID(int=0xA0 + index)) for index in range(5)]
SUBJECTS = ["ACME", "GLOBEX"]
STATUSES = [LifecycleState.RUNNING] * 4 + [LifecycleState.PAUSED, LifecycleState.FAILED]


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
    """Appends its name to a shared list each time it is called."""

    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.calls.append(self.name)
        return ()


def _declaration(rng: random.Random) -> frozenset[str]:
    choices = ["*", *(topic.value for topic in Topic)]
    choices += [f"bars:{asset}" for asset in ASSETS] + [f"quotes:{asset}" for asset in ASSETS]
    choices += [f"observations:{subject}" for subject in SUBJECTS]
    return frozenset(rng.sample(choices, rng.randint(1, 3)))


def _runtime(seed: int, count: int, calls: list[str]) -> RuntimeState:
    rng = random.Random(seed)
    strategies = {}
    for index in range(count):
        strategy_id = f"S{rng.randrange(10**6):06d}-{index}"
        strategies[strategy_id] = StrategyState(
            strategy_id,
            rng.choice(STATUSES),
            _Recorder(strategy_id, calls),
            subscriptions=_declaration(rng),
            started=True,
        )
    return RuntimeState(strategies=strategies)


def _asked(state: RuntimeState, topic: Topic, asset_id: str | None) -> tuple[str, ...]:
    """What dispatch asked before v3.12: every strategy, one by one."""

    return tuple(
        strategy_id
        for strategy_id, entry in state.strategies.items()
        if entry.routing.accepts(topic, asset_id)
    )


@pytest.mark.parametrize("seed", range(8))
def test_the_index_reaches_exactly_the_strategies_that_accept_in_their_order(seed: int) -> None:
    state = _runtime(seed, 60, [])

    for topic in Topic:
        for asset_id in (None, *ASSETS, *SUBJECTS, "not-held"):
            assert state.reach.reaching(topic, asset_id) == _asked(state, topic, asset_id)


@pytest.mark.parametrize("seed", range(4))
def test_dispatch_calls_the_strategies_it_called_before_in_the_same_order(seed: int) -> None:
    calls: list[str] = []
    state = _runtime(seed, 80, calls)

    for asset_id in ASSETS:
        calls.clear()
        StrategyEngine.process_event(state, _bar_event(asset_id), context_factory, 1.0)
        expected = [
            strategy_id
            for strategy_id in _asked(state, Topic.BARS, asset_id)
            if state.strategies[strategy_id].status is LifecycleState.RUNNING
        ]
        assert calls == expected


def test_a_change_the_runtime_makes_keeps_the_index_and_another_rebuilds_it() -> None:
    calls: list[str] = []
    strategies = {
        name: StrategyState(
            name,
            LifecycleState.RUNNING,
            _Recorder(name, calls),
            subscriptions=frozenset({f"bars:{ASSETS[0]}"}),
        )
        for name in ("first", "second")
    }
    state = RuntimeState(strategies=strategies)
    index = state.reach

    # The first dispatch delivers on_start: both states change, the index stays.
    started, _ = StrategyEngine.process_event(state, _bar_event(ASSETS[0]), context_factory, 1.0)
    assert started.strategies["first"].started
    assert started.reach is index

    # A state built by hand from changed subscriptions builds its own.
    moved = replace(
        started,
        strategies={
            **started.strategies,
            "second": replace(
                started.strategies["second"], subscriptions=frozenset({f"bars:{ASSETS[1]}"})
            ),
        },
    )
    assert moved.reach is not index
    assert moved.reach.reaching(Topic.BARS, ASSETS[1]) == ("second",)
    assert moved.reach.reaching(Topic.BARS, ASSETS[0]) == ("first",)


def test_an_evolution_that_changes_subscriptions_builds_its_own_index() -> None:
    first = StrategyState(
        "first", LifecycleState.RUNNING, _Recorder("first", []), subscriptions=frozenset({"bars"})
    )
    state = RuntimeState(strategies={"first": first})
    index = state.reach

    moved = replace(first, subscriptions=frozenset({"quotes"}))
    evolved = state.evolved({"first": moved}, (), ["first"])

    assert evolved.reach is not index
    assert evolved.reach.reaching(Topic.BARS, ASSETS[0]) == ()
    assert evolved.reach.reaching(Topic.QUOTES, ASSETS[0]) == ("first",)


def test_the_index_takes_no_part_in_a_states_value() -> None:
    built = _runtime(0, 10, [])
    fresh = RuntimeState(strategies=built.strategies, events=built.events)
    built.reach  # noqa: B018 -- built on first read

    assert built == fresh
    assert repr(built) == repr(fresh)


def test_a_copied_or_pickled_state_routes_as_the_original() -> None:
    """The index is derived: a copy or a pickle rebuilds it, and routes the same."""

    state = _runtime(3, 30, [])
    built = state.reach

    for other in (copy.deepcopy(state), pickle.loads(pickle.dumps(state))):
        # The strategies are copies (an instance compares by identity); their
        # ids, order and subscriptions are the original's.
        assert list(other.strategies) == list(state.strategies)
        for topic in Topic:
            for asset_id in (None, *ASSETS, *SUBJECTS):
                assert other.reach.reaching(topic, asset_id) == built.reaching(topic, asset_id)
    restored = pickle.loads(pickle.dumps(built))
    assert restored == built
    assert restored.reaching(Topic.BARS, ASSETS[0]) == built.reaching(Topic.BARS, ASSETS[0])


def test_overlapping_subscriptions_reach_a_strategy_once() -> None:
    routing = Subscriptions.parse({"*", "bars", f"bars:{ASSETS[0]}"})
    other = Subscriptions.parse({f"bars:{ASSETS[0]}"})
    index = RoutingIndex.of([("both", routing), ("one", other), ("all", routing)])

    assert index.reaching(Topic.BARS, ASSETS[0]) == ("both", "one", "all")
    assert index.reaching(Topic.BARS, ASSETS[1]) == ("both", "all")
    assert index.reaching(Topic.FILLS) == ("both", "all")
