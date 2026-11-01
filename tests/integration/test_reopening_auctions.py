"""A halt that reopens by auction (phase 3, ledger FUT-027).

A :class:`TradingHalt` declaring ``auction=True`` reopens by a single-price
cross: at the asset's first event after the halt ends, every order that rested
through it -- each market order, and each limit order the price reaches -- fills
at that event's opening print, a bar's open, as a maker; a limit the price does
not reach takes no part and rests on into continuous trading. A halt declaring
no auction reopens as before: each order at its own turn.

ACME trades as minute bars from 2.0; the halt runs from 2.5 to 4.5, and the
first bar after it opens at 103 and closes at 106.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.backtesting import BacktestEngine, BacktestResult, ReplayBacktest, RunSchedule
from alphalab.backtesting.dataset import MarketDataset
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.execution.policy import FillTiming
from alphalab.instrument.controls import (
    BandAction,
    PriceBand,
    TradingControls,
    TradingHalt,
    controls_from_primitives,
    controls_to_primitives,
)
from alphalab.lifecycle import digest_run
from alphalab.market.bar import Bar, TimeFrame
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration import test_trading_controls as controls
from tests.integration.harness import context_factory, running_strategy_state

D = Decimal
ACME = controls.ACME


def halt(auction: bool = True, **kw: Any) -> TradingControls:
    return TradingControls(
        halts=(TradingHalt(2.5, 4.5, asset_id=ACME.asset_id, auction=auction),), **kw
    )


#: (open, close) per bar; the 5.0 bar is the first after the halt.
BARS: dict[float, tuple[str, str]] = {
    2.0: ("100", "100"),
    3.0: ("101", "101"),
    4.0: ("102", "102"),
    5.0: ("103", "106"),
}
BARS_AFTER: dict[float, tuple[str, str]] = {**BARS, 6.0: ("104", "99"), 7.0: ("98", "97")}


def bars(series: dict[float, tuple[str, str]], volume: str = "1000000") -> MarketDataset:
    records = [
        Bar(
            ACME.asset_id,
            at,
            D(o),
            max(D(o), D(c)),
            min(D(o), D(c)),
            D(c),
            D(volume),
            None,
            None,
            TimeFrame.M1,
        )
        for at, (o, c) in series.items()
    ]
    return MarketDataset.of("REOPEN", records)


class OnBars(controls.Script):
    """The controls' Script, on bars: its scripted intents at each bar."""

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        at = event.bar.timestamp
        return self._script.get((at, event.bar.asset_id), ())


def run(
    script: dict[tuple[float, str], tuple[Intent, ...]],
    declared: TradingControls,
    series: dict[float, tuple[str, str]] = BARS,
    *,
    volume: str = "1000000",
    **pipeline: Any,
) -> tuple[BacktestResult, OnBars]:
    strategy = OnBars(script)
    retention = pipeline.pop("retention", None)
    base = controls.config(ACME, controls=declared, **pipeline)
    cfg = replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))
    if retention is not None:
        cfg = replace(cfg, pipeline=replace(cfg.pipeline, retention=retention))
    result = BacktestEngine.run(
        cfg,
        bars(series, volume),
        running_strategy_state("S", strategy),
        context_factory,
        schedule=RunSchedule.of(),
    )
    return result, strategy


def limit(price: str) -> OrderTerms:
    return OrderTerms.limit(D(price), TimeInForce.GTC)


IN_THE_HALT: dict[tuple[float, str], tuple[Intent, ...]] = {
    (3.0, ACME.asset_id): (
        controls.buy(3.0, "10"),
        controls.buy(3.0, "5", terms=limit("105")),
        controls.buy(3.0, "7", terms=limit("100")),
    )
}


# --------------------------------------------------------------------------- #
# One price for the orders that waited
# --------------------------------------------------------------------------- #


def test_orders_resting_through_the_halt_cross_at_the_reopening_print() -> None:
    result, _ = run(IN_THE_HALT, halt())
    # The market order and the limit at 105 cross at the 5.0 bar's open, 103;
    # the limit at 100 is not reached by the auction and takes no part.
    assert controls.trades(result) == [
        (5.0, D("10.000000"), D("103.00")),
        (5.0, D("5.000000"), D("103.00")),
    ]
    resting = [o for o in result.state.oms.orders.orders() if o.is_open]
    assert [o.limit_price for o in resting] == [D("100")]


def test_without_an_auction_each_order_trades_at_its_own_turn() -> None:
    result, _ = run(IN_THE_HALT, halt(auction=False))
    # Continuous trading at the 5.0 bar: the market order takes its close.
    assert (5.0, D("10.000000"), D("106.00")) in controls.trades(result)
    assert (5.0, D("10.000000"), D("103.00")) not in controls.trades(result)


def test_a_limit_the_auction_did_not_reach_trades_on_in_continuous_trading() -> None:
    result, _ = run(IN_THE_HALT, halt(), BARS_AFTER)
    # The limit at 100 rests past the auction and fills when the market trades
    # through it, in continuous trading: at its limit, in the 6.0 bar.
    assert (6.0, D("7.000000"), D("100")) in controls.trades(result)


def test_an_order_placed_after_the_reopening_trades_continuously() -> None:
    script = {**IN_THE_HALT, (5.0, ACME.asset_id): (controls.buy(5.0, "3"),)}
    result, _ = run(script, halt(), BARS_AFTER)
    # Placed at 5.0 after the auction, it trades at the next bar's close.
    assert (6.0, D("3.000000"), D("99.00")) in controls.trades(result)


def test_a_market_orders_remainder_after_the_auction_is_withdrawn() -> None:
    from alphalab.execution.policy import LiquidityCappedFill

    # The bars print 40 shares, a quarter of it ours: 10 of the 25 asked.
    result, _ = run(
        {(3.0, ACME.asset_id): (controls.buy(3.0, "25"),)},
        halt(),
        BARS_AFTER,
        volume="40",
        fill_policy=LiquidityCappedFill(D("0.25")),
    )
    assert controls.trades(result) == [(5.0, D("10"), D("103.00"))]
    assert not [o for o in result.state.oms.orders.orders() if o.is_open]


def test_a_short_market_history_never_crosses_an_order_placed_after_the_reopening() -> None:
    from alphalab.runtime.retention import RetentionPolicy

    peer = controls.PEER
    script = {(5.0, ACME.asset_id): (controls.buy(5.0, "3"),)}
    strategy = OnBars(script)
    base = controls.config(ACME, peer, controls=halt())
    cfg = replace(
        base,
        pipeline=replace(
            base.pipeline,
            fill_timing=FillTiming.NEXT_EVENT,
            retention=RetentionPolicy(market_history=1),
        ),
    )
    records = [
        *(record.payload for record in bars(BARS_AFTER).records),
        *(
            Bar(peer.asset_id, at, D("50"), D("50"), D("50"), D("50"), D("1000000"),
                None, None, TimeFrame.M1)
            for at in (5.5, 5.6)
        ),
    ]  # fmt: skip
    dataset = MarketDataset.of("REOPEN", sorted(records, key=lambda bar: bar.timestamp))
    result = BacktestEngine.run(
        cfg, dataset, running_strategy_state("S", strategy), context_factory
    )
    # PEER's bars push ACME's 5.0 bar out of a history kept to one event, so
    # 6.0 cannot be told from the first bar after the halt; the order placed
    # at 5.0 did not rest through it, and trades at 6.0's close, continuously.
    assert controls.trades(result) == [(6.0, D("3.000000"), D("99.00"))]


def test_an_auction_priced_outside_the_band_does_not_cross() -> None:
    banded = halt(bands=(PriceBand(ACME.asset_id, 1.0, None, D("90"), D("102.5"), BandAction.CAP),))
    result, _ = run({(3.0, ACME.asset_id): (controls.buy(3.0, "10"),)}, banded, BARS_AFTER)
    # The reopening print, 103, is beyond the limit up: nothing crosses at 5.0;
    # the order trades at the first event within the band, the 6.0 bar, in
    # continuous trading at its close.
    assert controls.trades(result) == [(6.0, D("10.000000"), D("99.00"))]


# --------------------------------------------------------------------------- #
# Persistence and replay
# --------------------------------------------------------------------------- #


def test_an_auction_reopening_round_trips_and_a_plain_halt_keeps_its_form() -> None:
    declared = halt()
    assert controls_from_primitives(controls_to_primitives(declared)) == declared
    plain = controls_to_primitives(halt(auction=False))
    assert plain is not None and "auction" not in plain["halts"][0]


def test_an_auction_reopening_replays_to_the_same_fills() -> None:
    base = controls.config(ACME, controls=halt())
    cfg = replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))
    backtest = BacktestEngine.run(
        cfg, bars(BARS), running_strategy_state("S", OnBars(IN_THE_HALT)), context_factory
    )
    replay = ReplayBacktest.run(
        cfg, bars(BARS), running_strategy_state("S", OnBars(IN_THE_HALT)), context_factory
    )
    assert replay.backtest.fills == backtest.fills
    assert digest_run(replay.backtest).result_id
