"""A venue's price bands and trading halts, declared as dated inputs (v4.2.0, ledger FUT-020).

A halt stops trading in an instrument, or in every instrument a venue lists, for
its interval: an order is accepted and rests -- a market order too -- and trades
at the first event after it, at that event's price. A band bounds where an
instrument trades: a limit beyond it is refused or capped as the band declares,
and nothing fills at an event priced outside it. Each is distinct from a missing
quote, a closed session and an absent fill.

ACME quotes 100 from 2.0, one quote a second; the strategy's orders are scripted.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import MARKET, OrderTerms, OrderType, TimeInForce
from alphalab.core.enums import AssetType, OrderStatus
from alphalab.execution.policy import LiquidityCappedFill
from alphalab.execution.queue import PriceTimeQueue
from alphalab.instrument.controls import (
    BandAction,
    PriceBand,
    TradingControls,
    TradingHalt,
    controls_from_primitives,
    controls_to_primitives,
)
from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import declare_trading_controls
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.types import TransactionType
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import (
    backtest_config,
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
    sized_quote,
)

D = Decimal
ACME = InstrumentRecord("ACME", AssetType.EQUITY, "XSHG", "CNY")
PEER = InstrumentRecord("PEER", AssetType.EQUITY, "XSHG", "CNY")
ELSE = InstrumentRecord("ELSE", AssetType.EQUITY, "XNSE", "CNY")
TRADES = (TransactionType.BUY, TransactionType.SELL)


class Script(BaseStrategy):
    """Returns scripted intents at chosen (instant, asset) quotes; keeps what it hears and sees."""

    def __init__(self, script: dict[tuple[float, str], tuple[Intent, ...]] | Any) -> None:
        self._script = script
        self.rejections: list[str] = []
        self.seen: list[tuple[float, bool, Any]] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        at, asset = event.quote.timestamp, event.quote.asset_id
        self.seen.append((at, context.universe.halted(asset), context.universe.price_band(asset)))
        return self._script.get((at, asset), ())

    def on_order(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.reason:
            self.rejections.append(event.reason)
        return ()


def buy(at: float, quantity: str = "10", terms: OrderTerms = MARKET, asset: Any = ACME) -> Intent:
    return Intent("S", asset.asset_id, D(quantity), timestamp=at, terms=terms)


def config(*records: InstrumentRecord, controls: TradingControls | None = None, **kw: Any) -> Any:
    base = backtest_config("S", **kw)
    registry = registry_of(*records)
    if controls is not None:
        registry = declare_trading_controls(registry, controls)
    pipeline = replace(base.pipeline, instruments=registry, currency="CNY")
    pipeline = replace(pipeline, account=replace(pipeline.account, base_currency="CNY"))
    pipeline = replace(pipeline, budget=pipeline.budget.in_currency("CNY"))
    return replace(base, pipeline=pipeline)


def data(mids: dict[str, dict[float, str]], size: str | None = None) -> MarketDataset:
    quotes = []
    for asset_id, series in mids.items():
        for at, mid in series.items():
            made = (
                quote(asset_id, at, D(mid))
                if size is None
                else sized_quote(asset_id, at, D(mid), D(size))
            )
            quotes.append(replace(made, currency="CNY"))
    return MarketDataset.of("CONTROLS", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id)))


def flat(*at: float, mid: str = "100") -> dict[float, str]:
    return dict.fromkeys(at, mid)


def run(
    script: dict[tuple[float, str], tuple[Intent, ...]] | Any,
    mids: dict[str, dict[float, str]],
    controls: TradingControls | None,
    *records: InstrumentRecord,
    size: str | None = None,
    **kw: Any,
) -> tuple[BacktestResult, Script]:
    strategy = Script(script)
    queue = kw.pop("queue", None)
    extra = {} if queue is None else {"queue": queue}
    result = BacktestEngine.run(
        config(*(records or (ACME,)), controls=controls, **kw),
        data(mids, size),
        running_strategy_state("S", strategy),
        context_factory,
        schedule=RunSchedule.of(),
        **extra,
    )
    return result, strategy


def trades(result: BacktestResult, record: Any = ACME) -> list[tuple[float, Decimal, Decimal]]:
    return [
        (t.timestamp, t.quantity, t.price)
        for t in result.state.portfolio.ledger.transactions
        if t.asset_id == record.asset_id and t.type in TRADES
    ]


HALT = TradingControls(
    halts=(TradingHalt(2.5, 4.5, asset_id=ACME.asset_id, reason="pending news"),)
)


# --------------------------------------------------------------------------- #
# Halts
# --------------------------------------------------------------------------- #


def test_a_market_order_in_a_halt_rests_and_trades_at_the_first_event_after_it() -> None:
    mids = {ACME.asset_id: {2.0: "100", 3.0: "101", 4.0: "102", 5.0: "103"}}
    result, strategy = run({(3.0, ACME.asset_id): (buy(3.0),)}, mids, HALT)
    # Placed at 3.0 in the halt; the 4.0 quote is still in it; it trades at 5.0.
    assert trades(result) == [(5.0, D("10.000000"), D("103.00"))]
    assert not strategy.rejections


def test_a_working_limit_does_not_fill_through_a_halt_and_does_after_it() -> None:
    bid = OrderTerms.limit(D("99"), TimeInForce.GTC)
    mids = {ACME.asset_id: {2.0: "100", 3.0: "95", 4.0: "95", 5.0: "98", 6.0: "100"}}
    result, _ = run({(2.0, ACME.asset_id): (buy(2.0, terms=bid),)}, mids, HALT)
    # The market falls through 99 at 3.0 and 4.0, inside the halt; it trades
    # through it again at 5.0, after.
    assert trades(result) == [(5.0, D("10.000000"), D("99"))]


def test_a_venue_halt_stops_what_it_lists_and_nothing_else() -> None:
    venue = TradingControls(halts=(TradingHalt(2.5, 4.5, venue="XSHG"),))
    script = {
        (3.0, ACME.asset_id): (buy(3.0),),
        (3.0, PEER.asset_id): (buy(3.0, asset=PEER),),
        (3.0, ELSE.asset_id): (buy(3.0, asset=ELSE),),
    }
    times = flat(2.0, 3.0, 4.0, 5.0)
    result, _ = run(
        script,
        {ACME.asset_id: times, PEER.asset_id: times, ELSE.asset_id: times},
        venue,
        ACME,
        PEER,
        ELSE,
    )
    assert [t for t, _, _ in trades(result, ACME)] == [5.0]
    assert [t for t, _, _ in trades(result, PEER)] == [5.0]
    assert [t for t, _, _ in trades(result, ELSE)] == [3.0]


def test_an_order_to_trade_now_or_never_is_cancelled_in_a_halt() -> None:
    ioc = OrderTerms.limit(D("101"), TimeInForce.IOC)
    mids = {ACME.asset_id: flat(2.0, 3.0, 4.0, 5.0)}
    result, _ = run({(3.0, ACME.asset_id): (buy(3.0, terms=ioc),)}, mids, HALT)
    assert trades(result) == []
    (order,) = result.state.oms.orders.orders()
    assert order.status is OrderStatus.CANCELLED


def test_a_strategy_sees_a_halt_and_a_band_in_force_now_and_nothing_later() -> None:
    controls = TradingControls(
        bands=(PriceBand(ACME.asset_id, 3.5, None, D("90"), D("110"), BandAction.REFUSE),),
        halts=HALT.halts,
    )
    _, strategy = run({}, {ACME.asset_id: flat(2.0, 3.0, 4.0, 5.0)}, controls)
    assert strategy.seen == [
        (2.0, False, None),
        (3.0, True, None),
        (4.0, True, (D("90"), D("110"))),
        (5.0, False, (D("90"), D("110"))),
    ]


# --------------------------------------------------------------------------- #
# Price bands
# --------------------------------------------------------------------------- #


def band(action: BandAction, start: float = 1.0) -> TradingControls:
    return TradingControls(
        bands=(PriceBand(ACME.asset_id, start, None, D("90"), D("110"), action),)
    )


def test_a_limit_beyond_a_refusing_band_is_refused_naming_the_band() -> None:
    high = OrderTerms.limit(D("115"))
    result, strategy = run(
        {(2.0, ACME.asset_id): (buy(2.0, terms=high),)},
        {ACME.asset_id: flat(2.0, 3.0)},
        band(BandAction.REFUSE),
    )
    assert trades(result) == []
    (reason,) = strategy.rejections
    assert "outside the venue's price band" in reason and "90 to 110" in reason


def test_a_limit_beyond_a_capping_band_is_capped_to_its_edge() -> None:
    low = OrderTerms.limit(D("80"), TimeInForce.GTC)  # a sell 10 limited at 80
    sell = Intent("S", ACME.asset_id, D("-10"), timestamp=2.0, terms=low)
    mids = {ACME.asset_id: {2.0: "100", 3.0: "95", 4.0: "88"}}
    result, strategy = run({(2.0, ACME.asset_id): (sell,)}, mids, band(BandAction.CAP))
    (order,) = result.state.oms.orders.orders()
    assert order.limit_price == D("90")
    assert not strategy.rejections
    # Capped to 90: it sells as the market meets 90, at 95, as a taker would.
    assert trades(result)[0][1] == D("-10.000000")


def test_nothing_fills_at_an_event_priced_outside_the_band() -> None:
    mids = {ACME.asset_id: {2.0: "100", 3.0: "120", 4.0: "108"}}
    result, strategy = run({(3.0, ACME.asset_id): (buy(3.0),)}, mids, band(BandAction.REFUSE))
    # The 3.0 print at 120 is beyond the limit up: the market order rests and
    # trades at 4.0, back within the band.
    assert trades(result) == [(4.0, D("10.000000"), D("108.00"))]
    assert not strategy.rejections


def test_at_a_locked_limit_a_market_order_fills_only_what_the_event_shows() -> None:
    # Limit up at 110, the market there, 4 shown: a buy for 10 takes the 4, and
    # the market order's remainder is withdrawn as any market order's is.
    mids = {ACME.asset_id: {2.0: "110", 3.0: "110"}}
    result, _ = run(
        {(2.0, ACME.asset_id): (buy(2.0),)},
        mids,
        band(BandAction.REFUSE),
        size="4",
        fill_policy=LiquidityCappedFill(D("1")),
    )
    assert trades(result) == [(2.0, D("4"), D("110.00"))]


def test_each_condition_is_reported_for_itself() -> None:
    other = InstrumentRecord("NOQT", AssetType.EQUITY, "XSHG", "CNY")
    high = OrderTerms.limit(D("115"))
    script = {
        # No quote has ever been seen for NOQT.
        (2.0, ACME.asset_id): (buy(2.0, asset=other), buy(2.0, terms=high)),
    }
    _, strategy = run(script, {ACME.asset_id: flat(2.0, 3.0)}, band(BandAction.REFUSE), ACME, other)
    assert any("No market price" in r for r in strategy.rejections)
    assert any("price band" in r for r in strategy.rejections)
    assert len(strategy.rejections) == 2


# --------------------------------------------------------------------------- #
# Margin, algorithms and queues under the controls
# --------------------------------------------------------------------------- #


def test_a_liquidation_in_a_halt_rests_through_it_and_is_called_once() -> None:
    record = replace(margin.ACME, exchange="XNAS")
    halt = TradingControls(halts=(TradingHalt(3.5, 5.5, asset_id=record.asset_id),))
    cfg = margin.config(record)
    cfg = replace(
        cfg,
        pipeline=replace(
            cfg.pipeline, instruments=declare_trading_controls(cfg.pipeline.instruments, halt)
        ),
    )
    result = BacktestEngine.run(
        cfg,
        margin.prices({record.asset_id: margin.series("100", "170", "170", "170", "170")}),
        running_strategy_state("S", margin.Trader(margin.short(record))),
        context_factory,
        schedule=RunSchedule.of(),
    )
    # Called at 3.0; the liquidation's next turn at 4.0 and 5.0 is in the halt;
    # it buys back at 6.0. One call: the working liquidation is not placed again.
    (call,) = margin.calls(result)
    assert call.timestamp == 3.0
    assert margin.fills(result, record) == [(2.0, D("-100")), (6.0, D("65"))]


def test_an_algorithms_children_do_not_trade_in_a_halt_and_the_schedule_goes_on() -> None:
    from tests.integration import test_algorithms_on_the_historical_path as algorithms

    halt = TradingControls(
        halts=(TradingHalt(algorithms.at(1), algorithms.at(4), asset_id=algorithms.ACME),)
    )
    cfg = algorithms.config()
    cfg = replace(
        cfg,
        pipeline=replace(
            cfg.pipeline, instruments=declare_trading_controls(cfg.pipeline.instruments, halt)
        ),
    )
    result = BacktestEngine.run(
        cfg,
        algorithms.bars(),
        algorithms.strategies(algorithms.BUY_100),
        context_factory,
        schedule=RunSchedule(),
        execution=algorithms.TWAP_PLAN,
    )
    filled_at = [
        round((t.timestamp - algorithms.T0) / algorithms.MINUTE)
        for t in result.state.portfolio.ledger.transactions
        if t.type in TRADES
    ]
    # Children due inside minutes 1 to 3 do not trade; those after do.
    assert filled_at and all(not 1 <= m < 4 for m in filled_at)


def test_a_queued_bid_does_not_fill_in_a_halt() -> None:
    bid = OrderTerms.limit(D("99"), TimeInForce.GTC)
    mids = {ACME.asset_id: {2.0: "100", 3.0: "95", 4.0: "95"}}
    result, _ = run(
        {(2.0, ACME.asset_id): (buy(2.0, terms=bid),)},
        mids,
        HALT,
        size="1000",
        queue=PriceTimeQueue(),
    )
    assert trades(result) == []


# --------------------------------------------------------------------------- #
# Declaration, persistence, replay and identity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: PriceBand(ACME.asset_id, 2.0, 1.0, D("90"), D("110"), BandAction.CAP),
        lambda: PriceBand(ACME.asset_id, 1.0, None, D("110"), D("90"), BandAction.CAP),
        lambda: TradingHalt(2.0, 1.0, asset_id=ACME.asset_id),
        lambda: TradingHalt(1.0, 2.0),
        lambda: TradingHalt(1.0, 2.0, asset_id=ACME.asset_id, venue="XSHG"),
        lambda: TradingControls(
            bands=(
                PriceBand(ACME.asset_id, 1.0, 5.0, D("90"), D("110"), BandAction.CAP),
                PriceBand(ACME.asset_id, 4.0, None, D("95"), D("105"), BandAction.CAP),
            )
        ),
    ],
)
def test_controls_that_cannot_hold_are_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_controls_naming_what_the_registry_cannot_trade_are_refused() -> None:
    with pytest.raises(InstrumentRegistrationError, match="does not hold"):
        declare_trading_controls(registry_of(PEER), band(BandAction.CAP))
    with pytest.raises(InstrumentRegistrationError, match="no registered instrument"):
        declare_trading_controls(
            registry_of(ACME), TradingControls(halts=(TradingHalt(1.0, 2.0, venue="XLON"),))
        )


def test_controls_round_trip_through_the_registry_snapshot() -> None:
    controls = TradingControls(
        bands=(PriceBand(ACME.asset_id, 1.0, 9.0, D("90"), D("110"), BandAction.CAP),),
        halts=(*HALT.halts, TradingHalt(7.0, None, venue="XSHG", reason="market-wide")),
    )
    assert controls_from_primitives(controls_to_primitives(controls)) == controls
    registry = declare_trading_controls(registry_of(ACME), controls)
    payload = deserialize(serialize(capture_registry(registry)))
    assert restore_registry(registry_from_primitives(payload)).controls == controls


def test_a_halted_run_replays_and_restores_from_every_pause() -> None:
    script = {(3.0, ACME.asset_id): (buy(3.0),)}
    mids = {ACME.asset_id: {2.0: "100", 3.0: "101", 4.0: "102", 5.0: "103", 6.0: "104"}}
    cfg, dataset = config(ACME, controls=HALT), data(mids)
    whole = ReplayBacktest.run(
        cfg, dataset, running_strategy_state("S", Script(script)), context_factory
    )
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg,
            dataset,
            running_strategy_state("S", Script(script)),
            context_factory,
            pause_when=margin._at(boundary),
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": Script(script)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, dataset)
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference


def test_controls_that_never_bind_change_nothing_recorded() -> None:
    script = {(2.0, ACME.asset_id): (buy(2.0),)}
    mids = {ACME.asset_id: flat(2.0, 3.0)}
    late = TradingControls(halts=(TradingHalt(50.0, 60.0, asset_id=ACME.asset_id),))
    plain, _ = run(script, mids, None)
    declared, _ = run(script, mids, late)
    assert digest_run(plain).result_id == digest_run(declared).result_id


# --------------------------------------------------------------------------- #
# At the edges: a halt's end, a stop in a halt, a band begun after placement
# --------------------------------------------------------------------------- #


def test_a_halt_ends_at_its_end_and_an_event_then_trades() -> None:
    until_four = TradingControls(halts=(TradingHalt(2.5, 4.0, asset_id=ACME.asset_id),))
    mids = {ACME.asset_id: {2.0: "100", 3.0: "101", 4.0: "102", 5.0: "103"}}
    result, _ = run({(3.0, ACME.asset_id): (buy(3.0),)}, mids, until_four)
    assert trades(result) == [(4.0, D("10.000000"), D("102.00"))]


def test_a_stop_is_not_set_off_by_a_price_in_a_halt() -> None:
    # A sell stop-limit, stop 95, limit 94: the market prints 90 inside the halt
    # and is back at 99 after it. Nothing traded at 90, so nothing reached the
    # stop: it is not triggered, and does not sell at 99 afterwards.
    stop = OrderTerms(
        order_type=OrderType.STOP_LIMIT,
        stop_price=D("95"),
        limit_price=D("94"),
        time_in_force=TimeInForce.GTC,
    )
    sell = Intent("S", ACME.asset_id, D("-10"), timestamp=2.0, terms=stop)
    mids = {ACME.asset_id: {2.0: "100", 3.0: "90", 4.0: "90", 5.0: "99"}}
    result, _ = run({(2.0, ACME.asset_id): (sell,)}, mids, HALT)
    assert trades(result) == []
    (order,) = result.state.oms.orders.orders()
    assert order.is_open and order.triggered_at is None


def test_a_resting_limit_beyond_a_band_begun_after_it_is_placed_does_not_fill() -> None:
    # A bid at 115 placed at 2.0 under no band; from 2.5 the band is 90 to 110.
    # At 3.0 the market trades through 115 at 108: the bid would fill at its
    # limit, 115, beyond the limit up, and does not.
    bid = OrderTerms.limit(D("115"), TimeInForce.GTC)
    late = TradingControls(
        bands=(PriceBand(ACME.asset_id, 2.5, None, D("90"), D("110"), BandAction.REFUSE),)
    )
    mids = {ACME.asset_id: {2.0: "120", 3.0: "108", 4.0: "108"}}
    result, _ = run({(2.0, ACME.asset_id): (buy(2.0, terms=bid),)}, mids, late)
    assert trades(result) == []


def test_a_stop_reached_at_a_gap_beyond_the_band_does_not_trade_there() -> None:
    from alphalab.market.bar import Bar, TimeFrame

    # A sell stop at 95; the bar opens at 85, below the limit down of 90, and
    # closes at 92 within it. The stop would execute at the open where the
    # market was -- beyond the band -- so it does not trade at this bar.
    stop = OrderTerms(order_type=OrderType.STOP, stop_price=D("95"), time_in_force=TimeInForce.GTC)
    sell = Intent("S", ACME.asset_id, D("-10"), timestamp=2.0, terms=stop)

    def bar(at: float, o: str, h: str, low: str, c: str) -> Bar:
        return Bar(ACME.asset_id, at, D(o), D(h), D(low), D(c), D("1000"), None, None, TimeFrame.M1)

    bars = MarketDataset.of(
        "CONTROLS", [bar(2.0, "100", "101", "99", "100"), bar(3.0, "85", "93", "85", "92")]
    )

    class OnBar(Script):
        def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
            return self._script.get((event.bar.timestamp, event.bar.asset_id), ())

    strategy = OnBar({(2.0, ACME.asset_id): (sell,)})
    result = BacktestEngine.run(
        config(ACME, controls=band(BandAction.REFUSE)),
        bars,
        running_strategy_state("S", strategy),
        context_factory,
        schedule=RunSchedule.of(),
    )
    assert trades(result) == []
