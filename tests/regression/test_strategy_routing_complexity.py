"""Regression guard for PRF-010: an event costs the strategies it reaches, not the runtime's.

The v3.12 stress run measured every strategy costing about a third of a
microsecond on every record whatever it subscribed to: ten strategies trading
one asset took 665 microseconds a record alone, 1,012 beside 1,000 strategies
trading other assets and 4,097 beside 10,000. Dispatch asked every strategy in
turn; it now reads the runtime's routing index.

The first guard is exact: it counts what dispatch reads from the strategies'
mapping. The second times it, by the suite's one method (``_timing``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator, Mapping
from decimal import Decimal
from typing import Any

from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import BarClosed
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.engine import StrategyEngine
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.state import RuntimeState, StrategyState, StrategyStatus
from tests.integration.harness import context_factory
from tests.regression._timing import CLOCK, timings

TRADED = str(uuid.UUID(int=0xA))
SUBSCRIBED = 10


class _Quiet(BaseStrategy):
    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return ()


class _Counted(Mapping[str, StrategyState]):
    """A strategies mapping that counts what is read from it."""

    def __init__(self, held: dict[str, StrategyState]) -> None:
        self.held = held
        self.reads = 0
        self.walks = 0

    def __getitem__(self, key: str) -> StrategyState:
        self.reads += 1
        return self.held[key]

    def __iter__(self) -> Iterator[str]:
        self.walks += 1
        return iter(self.held)

    def __len__(self) -> int:
        return len(self.held)


def _strategies(idle: int) -> dict[str, StrategyState]:
    strategies: dict[str, StrategyState] = {}
    for index in range(SUBSCRIBED + idle):
        traded = TRADED if index < SUBSCRIBED else str(uuid.UUID(int=0x1000 + index))
        strategy_id = f"S{index:05d}"
        strategies[strategy_id] = StrategyState(
            strategy_id,
            StrategyStatus.RUNNING,
            _Quiet(),
            subscriptions=frozenset({f"bars:{traded}"}),
            started=True,
        )
    return strategies


def _bar_event(at: float) -> BarClosed:
    bar = Bar(
        asset_id=TRADED,
        timestamp=at,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal("1"),
        timeframe=TimeFrame.M1,
        vwap=None,
        trade_count=None,
    )
    return BarClosed(f"B-{at}", at, bar)


def test_dispatch_reads_only_the_strategies_an_event_reaches() -> None:
    counted = _Counted(_strategies(4_000))
    state = RuntimeState(strategies=counted)
    state.reach  # noqa: B018 -- the index is built once, on first read
    counted.reads = counted.walks = 0

    for step in range(20):
        StrategyEngine.process_event(state, _bar_event(1.0 + step), context_factory, 1.0)

    assert counted.walks == 0
    assert counted.reads == 20 * SUBSCRIBED


def test_the_cost_of_an_event_does_not_grow_with_strategies_it_does_not_reach() -> None:
    states = {idle: RuntimeState(strategies=_strategies(idle)) for idle in (100, 4_000)}
    events = [_bar_event(1.0 + step) for step in range(200)]

    def _elapsed(idle: int) -> float:
        state = states[idle]
        state.reach  # noqa: B018
        start = CLOCK()
        for event in events:
            StrategyEngine.process_event(state, event, context_factory, 1.0)
        return CLOCK() - start

    small, large = timings(_elapsed, 100, 4_000, rounds=3)

    # 40x the idle strategies. Asking each would cost about 1.4 ms more per
    # event at 4,000 -- several times the dispatch itself; reading the index
    # costs nothing more. The bound catches a reintroduced scan, not a constant.
    assert large < small * 2, f"dispatch grew with idle strategies: {small=} {large=}"
