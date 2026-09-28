"""A strategy can decide once per instant, on every price of it (EXE-004).

A strategy trading across instruments is dispatched once per record, so at any
one record it sees an instant part-way through: at the first of two quotes
sharing a timestamp, the second asset's price is the previous instant's. Until
v3.11 nothing told a strategy an instant was complete, so a cross-sectional
decision -- rank the universe, buy the cheapest -- was made on a mixture of two
instants. A slice is the instant complete: the driver closes it once every
record carrying its timestamp has been published, strategies subscribed to
``slices`` that define ``on_slice`` see every asset and price of it, and what
they ask for rests until each asset's next event.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.allocation.sizing import FixedQuantitySizing
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine, advance, finalize, initialize
from alphalab.backtesting.replay import ReplayBacktest
from alphalab.broker import BrokerEngine, PaperBroker
from alphalab.common.ids import id_scope
from alphalab.common.order_terms import OrderTerms
from alphalab.execution.costs import FREE
from alphalab.execution.policy import ImmediateFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.source import SequenceSource
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.broker_routing import RoutingConfig
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionRouting
from alphalab.runtime.live import LiveSession
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine, RunState
from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
from alphalab.runtime.session import TradingSession
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy import SliceClosed, defines_on_slice
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    quote,
    running_strategy_state,
)

STRATEGY = "SLICE"
X = "0f4f2c7a-8d1e-4b0a-9c3f-5e6d7a8b9c01"
Y = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c02"

#: Two assets at instants 2 and 3, one of them alone at 4 and at 5. At the first
#: record of instant 3, Y's price is still instant 2's.
QUOTES = (
    quote(X, 2.0, Decimal("100")),
    quote(Y, 2.0, Decimal("50")),
    quote(X, 3.0, Decimal("101")),
    quote(Y, 3.0, Decimal("49")),
    quote(X, 4.0, Decimal("102")),
    quote(Y, 5.0, Decimal("48")),
)
DATASET = MarketDataset.of("DS-SLICES", QUOTES)


class _CrossSection(BaseStrategy):
    """Records every slice it sees, and buys the cheapest asset of those planned."""

    def __init__(self, plan: Mapping[float, Decimal] | None = None) -> None:
        self.plan = dict(plan or {})
        self.slices: list[tuple[float, tuple[str, ...], dict[str, Decimal | None]]] = []
        self.quotes = 0

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.quotes += 1
        return ()

    def on_slice(self, context: StrategyContext, event: SliceClosed) -> Iterable[Intent]:
        prices = {asset: context.market.price(asset) for asset in event.assets}
        self.slices.append((event.timestamp, event.assets, prices))
        quantity = self.plan.get(event.timestamp)
        if quantity is None:
            return ()
        cheapest = min(event.assets, key=lambda asset: (prices[asset], asset))
        return (Intent(STRATEGY, cheapest, quantity, timestamp=event.timestamp),)


def _strategies(strategy: BaseStrategy, subscriptions: frozenset[str] = frozenset({"*"})) -> Any:
    return running_strategy_state(STRATEGY, strategy, subscriptions)


EVERY_SLICE = [
    (2.0, (X, Y), {X: Decimal("100"), Y: Decimal("50")}),
    (3.0, (X, Y), {X: Decimal("101"), Y: Decimal("49")}),
    (4.0, (X,), {X: Decimal("102")}),
    (5.0, (Y,), {Y: Decimal("48")}),
]


# --------------------------------------------------------------------------- #
# What a slice is
# --------------------------------------------------------------------------- #


def test_each_instant_is_closed_once_after_its_last_record_with_every_price() -> None:
    strategy = _CrossSection()

    BacktestEngine.run(backtest_config(STRATEGY), DATASET, _strategies(strategy), context_factory)

    assert strategy.slices == EVERY_SLICE
    assert strategy.quotes == len(QUOTES), "records are delivered as they always were"


def test_a_record_level_decision_sees_a_mixture_of_instants_and_the_slice_does_not() -> None:
    """The defect the hook exists for, shown rather than asserted in prose."""

    seen_at_records: list[tuple[float, Decimal | None]] = []

    class _PerRecord(_CrossSection):
        def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
            if event.quote.asset_id == X:
                seen_at_records.append((event.quote.timestamp, context.market.price(Y)))
            return ()

    strategy = _PerRecord()
    BacktestEngine.run(backtest_config(STRATEGY), DATASET, _strategies(strategy), context_factory)

    # At X's record of instant 3, Y is still quoted at instant 2's 50 ...
    assert (3.0, Decimal("50")) in seen_at_records
    # ... and the slice of instant 3 sees Y's own 49.
    assert strategy.slices[1][2][Y] == Decimal("49")


def test_a_slice_order_rests_and_fills_at_its_assets_next_event() -> None:
    strategy = _CrossSection({2.0: Decimal("10")})

    result = BacktestEngine.run(
        backtest_config(STRATEGY), DATASET, _strategies(strategy), context_factory
    )

    # Y was the cheaper at instant 2; the order was placed after instant 2's
    # records were worked, so it fills at Y's next event, instant 3, at 49.
    ((asset, at, price, quantity),) = [
        (fill.asset_id, fill.filled_at, fill.price, fill.quantity) for fill in result.fills
    ]
    assert (asset, at, price, quantity) == (Y, 3.0, Decimal("49"), Decimal("10"))


def test_an_auction_order_from_a_slice_takes_the_next_daily_open() -> None:
    bars = tuple(
        Bar(
            X,
            float(day),
            Decimal(o),
            Decimal(o) + 2,
            Decimal(o) - 2,
            Decimal(c),
            Decimal("100000"),
            None,
            None,
            TimeFrame.D1,
        )
        for day, (o, c) in enumerate((("100", "101"), ("103", "104")), start=2)
    )

    class _AtOpen(_CrossSection):
        def on_slice(self, context: StrategyContext, event: SliceClosed) -> Iterable[Intent]:
            return tuple(
                replace(intent, terms=OrderTerms.at_open())
                for intent in super().on_slice(context, event)
            )

    result = BacktestEngine.run(
        backtest_config(STRATEGY),
        MarketDataset.of("DS-BARS", bars),
        _strategies(_AtOpen({2.0: Decimal("5")})),
        context_factory,
    )

    # Decided at day 2's close; filled at day 3's opening auction.
    ((at, price),) = [(fill.filled_at, fill.price) for fill in result.fills]
    assert (at, price) == (3.0, Decimal("103"))


# --------------------------------------------------------------------------- #
# Who receives one, and parity for everyone else
# --------------------------------------------------------------------------- #


def test_only_a_strategy_defining_on_slice_is_asked() -> None:
    assert defines_on_slice(_CrossSection())
    assert not defines_on_slice(ScriptedStrategy(STRATEGY, X, {}))
    assert not defines_on_slice(object())


def test_a_strategy_not_subscribed_to_slices_is_not_called() -> None:
    quotes_only = _CrossSection()
    BacktestEngine.run(
        backtest_config(STRATEGY),
        DATASET,
        _strategies(quotes_only, frozenset({"quotes"})),
        context_factory,
    )
    slices_only = _CrossSection()
    BacktestEngine.run(
        backtest_config(STRATEGY),
        DATASET,
        _strategies(slices_only, frozenset({"slices"})),
        context_factory,
    )

    assert (quotes_only.slices, quotes_only.quotes) == ([], len(QUOTES))
    assert (slices_only.slices, slices_only.quotes) == (EVERY_SLICE, 0)


def _without_slices(config: RunConfig, strategies: Any) -> str:
    """The loop every backtest ran before v3.11: records only."""

    with id_scope(config.seed):
        state = replace(
            initialize(replace(config, mode=ExecutionMode.BACKTEST), strategies),
            source_id=DATASET.dataset_id,
        )
        for record in DATASET.records:
            state, _ = advance(state, record, context_factory)
        return serialize(capture(finalize(state).run))


def test_a_run_whose_strategies_define_no_on_slice_is_exactly_what_it_was() -> None:
    config = backtest_config(STRATEGY)
    plan = {2.0: Decimal("3"), 3.0: Decimal("-1")}

    def strategies() -> Any:
        return _strategies(ScriptedStrategy(STRATEGY, X, plan))

    with_slices = BacktestEngine.run(config, DATASET, strategies(), context_factory)

    assert serialize(capture(with_slices.run)) == _without_slices(config, strategies())
    assert with_slices.run.last_slice_at is None


def test_the_three_drivers_close_the_same_slices() -> None:
    config = backtest_config(STRATEGY)
    backtest, replayed, session = _CrossSection(), _CrossSection(), _CrossSection()

    BacktestEngine.run(config, DATASET, _strategies(backtest), context_factory)
    ReplayBacktest.run(config, DATASET, _strategies(replayed), context_factory)
    TradingSession.run(
        replace(config, mode=ExecutionMode.PAPER),
        SequenceSource.from_records(DATASET.dataset_id, DATASET.records),
        _strategies(session),
        context_factory,
    )

    assert backtest.slices == replayed.slices == session.slices == EVERY_SLICE


# --------------------------------------------------------------------------- #
# Closed once, by a driver that cannot look ahead
# --------------------------------------------------------------------------- #


def _two_records_at_instant_two(strategy: _CrossSection) -> RunState:
    config = backtest_config(STRATEGY)
    state = RunEngine.initialize(config, _strategies(strategy))
    for record in DATASET.records[:2]:
        state, _ = RunEngine.advance(state, record, context_factory)
    return state


def test_before_closes_an_instant_only_once_the_next_one_has_begun() -> None:
    strategy = _CrossSection()
    state = _two_records_at_instant_two(strategy)

    # Another record at instant 2 may still come: nothing is closed.
    assert RunEngine.close_slice(state, context_factory, before=2.0) is state
    closed = RunEngine.close_slice(state, context_factory, before=3.0)

    assert [entry[0] for entry in strategy.slices] == [2.0]
    assert closed.last_slice_at == 2.0


def test_a_slice_is_closed_once_even_across_a_restart() -> None:
    strategy = _CrossSection()
    state = RunEngine.close_slice(_two_records_at_instant_two(strategy), context_factory)

    # Asked again: nothing.
    assert RunEngine.close_slice(state, context_factory) is state
    # And a restored run knows it already closed that instant.
    objects = RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=FixedQuantitySizing(),
            simulator=ExecutionSimulator(),
            strategies={STRATEGY: strategy},
        ),
        fill_policy=ImmediateFill(),
    )
    restored = restore(from_primitives(deserialize(serialize(capture(state)))), objects)
    assert restored.last_slice_at == 2.0
    assert RunEngine.close_slice(restored, context_factory) is restored
    assert [entry[0] for entry in strategy.slices] == [2.0]


def test_a_pipeline_with_nothing_published_closes_nothing() -> None:
    strategy = _CrossSection()
    state = RunEngine.initialize(backtest_config(STRATEGY), _strategies(strategy))

    assert RunEngine.close_slice(state, context_factory) is state
    assert ExecutionPipeline.close_slice(state.pipeline, context_factory)[1:] == ((), ())


# --------------------------------------------------------------------------- #
# Live: the caller says when, and the orders reach the venue
# --------------------------------------------------------------------------- #


def test_a_live_session_routes_what_a_slice_asks_for() -> None:
    config = backtest_config(STRATEGY)
    live = RunConfig(
        pipeline=replace(config.pipeline, routing=ExecutionRouting.EXTERNAL),
        mode=ExecutionMode.LIVE,
        seed=7,
        start_timestamp=1.0,
        compile_analytics=False,
    )
    venue = PaperBroker(FREE)
    strategy = _CrossSection({2.0: Decimal("4")})
    state = LiveSession.initialize(
        live,
        _strategies(strategy),
        BrokerEngine.initialize("VENUE-X", Decimal("1000000"), "USD"),
        RoutingConfig(venue="VENUE-X", currency="USD"),
    )
    state, _ = LiveSession.connect(state, venue, 1.5)
    for record in DATASET.records[:2]:
        state, step = LiveSession.advance(state, record, context_factory, venue)
        assert step.routed == ()

    state, routed, _ = LiveSession.close_slice(state, venue, context_factory, now=2.5)

    ((attempt,),) = (routed,)
    assert attempt.routed
    (order,) = state.run.pipeline.oms.orders.orders()
    assert (order.asset_id, order.quantity) == (Y, Decimal("4"))
    # Closed once: a second call at the same instant sends nothing more.
    _, again, _ = LiveSession.close_slice(state, venue, context_factory, now=2.6)
    assert again == ()
