"""A reopening auction computed from the venue's published book (phase 4, ledger FUT-033).

A halt declaring an :class:`AuctionUncross` computes its reopening price rather
than reading it from the first event's print: from the :class:`AuctionInterest`
the venue published during the halt and the run's own orders that waited for
the auction, the price that matches the most volume, then the smallest
imbalance, then the declared tie-break. Only the run's own orders fill, at that
price, by price and then by the declared priority at the marginal price.
Nothing crosses without a book, without matched volume, or outside the band --
unless the venue declares an extension, when nothing trades until the auction
is held again. Each instrument's reopening is recorded on the run, so however
little market history it keeps, a later event is never taken for the first.

ACME trades as minute bars from 2.0; the halt runs from 2.5 to 4.5, and the
first bar after it opens at 103 and closes at 106 (the helpers are
tests/integration/test_reopening_auctions.py's).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import BacktestEngine, PausedReplay, ReplayBacktest, RunSchedule
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.conventions.tick import TickSchedule
from alphalab.execution.policy import FillTiming
from alphalab.instrument import (
    AuctionInterest,
    AuctionPriority,
    AuctionTieBreak,
    AuctionUncross,
    BandAction,
    BandReference,
    PriceBand,
    TradingControls,
    TradingHalt,
    declare_trading_controls,
)
from alphalab.instrument.controls import controls_from_primitives, controls_to_primitives
from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.lifecycle import digest_run
from alphalab.market.bar import Bar, TimeFrame
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.retention import RetentionPolicy
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.events import Intent
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_reopening_auctions as reopening
from tests.integration import test_trading_controls as controls
from tests.integration.harness import context_factory, running_strategy_state

D = Decimal
Script = dict[tuple[float, str], tuple[Intent, ...]]
ACME = controls.ACME
BARS = reopening.BARS
BARS_AFTER = reopening.BARS_AFTER


def book(
    at: float = 3.0,
    bids: tuple[tuple[str, str], ...] = (),
    offers: tuple[tuple[str, str], ...] = (),
    **kw: str,
) -> AuctionInterest:
    return AuctionInterest(
        ACME.asset_id,
        at,
        tuple((D(p), D(q)) for p, q in bids),
        tuple((D(p), D(q)) for p, q in offers),
        **{k: D(v) for k, v in kw.items()},
    )


def computed(
    *books: AuctionInterest,
    tie_break: AuctionTieBreak = AuctionTieBreak.HIGHEST,
    priority: AuctionPriority = AuctionPriority.PUBLISHED_FIRST,
    extension: float | None = None,
    **kw: Any,
) -> TradingControls:
    rule = AuctionUncross(tie_break, priority, extension)
    halt = TradingHalt(2.5, 4.5, asset_id=ACME.asset_id, auction=True, uncross=rule)
    return TradingControls(halts=(halt,), auction_books=books, **kw)


def limit(price: str) -> Any:
    return reopening.limit(price)


def reopened(result: Any) -> Any:
    return result.state.execution.reopened.get(ACME.asset_id)


# --------------------------------------------------------------------------- #
# Price discovery
# --------------------------------------------------------------------------- #


def test_the_price_is_the_one_matching_the_most_volume_and_the_run_fills_there() -> None:
    published = book(bids=(("103", "50"), ("101", "30")), offers=(("100", "40"), ("102", "40")))
    script: Script = {
        (3.0, ACME.asset_id): (
            controls.buy(3.0, "10"),
            controls.buy(3.0, "20", terms=limit("102")),
        )
    }
    result, _ = reopening.run(script, computed(published))
    # At 102: demand 50 + the run's 10 and 20 = 80, supply 40 + 40 = 80 -- the
    # most volume any price matches, and balanced. Not the print's 103.
    assert controls.trades(result) == [
        (5.0, D("10.000000"), D("102")),
        (5.0, D("20.000000"), D("102")),
    ]
    held = reopened(result)
    assert (held.price, held.matched, held.imbalance) == (D("102"), D("80"), D("0"))


def test_of_equal_volumes_the_smaller_imbalance_wins_and_a_limit_beyond_it_rests() -> None:
    published = book(bids=(("103", "50"), ("101", "30")), offers=(("100", "40"), ("102", "20")))
    script: Script = {
        (3.0, ACME.asset_id): (
            controls.buy(3.0, "10"),
            controls.buy(3.0, "20", terms=limit("102")),
        )
    }
    result, _ = reopening.run(script, computed(published))
    # 102 and 103 both match 60; at 103 nothing is left over. The limit at 102
    # is not reached by 103: it takes no part, and rests on.
    assert controls.trades(result) == [(5.0, D("10.000000"), D("103"))]
    assert [o.limit_price for o in result.state.oms.orders.orders() if o.is_open] == [D("102")]
    assert reopened(result).imbalance == D("0")


@pytest.mark.parametrize(
    ("rule", "refs", "price"),
    [
        (AuctionTieBreak.HIGHEST, (), "102"),
        (AuctionTieBreak.LOWEST, (), "100"),
        (
            AuctionTieBreak.NEAREST_REFERENCE,
            (BandReference(ACME.asset_id, 1.0, D("101.4")),),
            "102",
        ),
        (
            AuctionTieBreak.NEAREST_REFERENCE,
            (BandReference(ACME.asset_id, 1.0, D("100.6")),),
            "100",
        ),
    ],
)
def test_a_tie_is_broken_by_the_declared_rule(
    rule: AuctionTieBreak, refs: tuple[BandReference, ...], price: str
) -> None:
    # 100 and 102 each match 25 and leave 25 over: a tie the rule breaks.
    published = book(bids=(("102", "30"),), offers=(("100", "25"),))
    script: Script = {(3.0, ACME.asset_id): (controls.buy(3.0, "20", terms=limit("102")),)}
    result, _ = reopening.run(script, computed(published, tie_break=rule, references=refs))
    assert reopened(result).price == D(price)


# --------------------------------------------------------------------------- #
# Allocation: by price, then the declared priority
# --------------------------------------------------------------------------- #


def test_behind_published_interest_the_run_fills_only_what_is_left_at_its_price() -> None:
    published = book(bids=(("102", "30"),), offers=(("100", "25"),))
    script: Script = {(3.0, ACME.asset_id): (controls.buy(3.0, "20", terms=limit("102")),)}
    result, _ = reopening.run(script, computed(published))
    # 25 matched at 102; the published 30 there is ahead of the run's 20, so
    # the run's bid fills nothing and rests on.
    assert controls.trades(result) == []
    (bid,) = [o for o in result.state.oms.orders.orders() if o.is_open]
    assert bid.filled_quantity == D("0")


def test_pro_rata_the_run_shares_what_is_left_at_its_price() -> None:
    published = book(bids=(("102", "30"),), offers=(("100", "25"),))
    script: Script = {(3.0, ACME.asset_id): (controls.buy(3.0, "20", terms=limit("102")),)}
    result, _ = reopening.run(script, computed(published, priority=AuctionPriority.PRO_RATA))
    # 25 of the 50 bid at 102 matched: the run's 20 takes 20 * 25 / 50 = 10.
    assert controls.trades(result) == [(5.0, D("10"), D("102"))]


def test_a_better_price_fills_before_the_marginal_one_and_a_market_order_first() -> None:
    published = book(bids=(("102", "10"),), offers=(("100", "25"),), market_buy="5")
    script: Script = {
        (2.0, ACME.asset_id): (controls.buy(2.0, "4", terms=limit("104")),),
        (3.0, ACME.asset_id): (controls.buy(3.0, "6"), controls.buy(3.0, "8", terms=limit("102"))),
    }
    result, _ = reopening.run(script, computed(published), BARS_AFTER)
    # 25 offered, 33 bid at 102: market 5 + 6 first, then the bid at 104 (placed
    # before the halt, and eligible as one that waited), then at 102 the
    # published 10 ahead of the run's 8 -- which takes nothing of the 25.
    assert (5.0, D("6.000000"), D("102")) in controls.trades(result)
    assert (5.0, D("4.000000"), D("102")) in controls.trades(result)
    at_auction = [t for t in controls.trades(result) if t[0] == 5.0]
    assert sorted(t[1] for t in at_auction) == [D("4.000000"), D("6.000000")]
    # It rests on, and trades in continuous trading at 6.0, at its limit.
    assert (6.0, D("8.000000"), D("102")) in controls.trades(result)


def test_an_order_placed_after_the_halt_ended_trades_continuously_not_in_the_auction() -> None:
    peer = controls.PEER
    published = book(bids=(("103", "50"),), offers=(("100", "40"), ("102", "40")))
    script: Script = {
        (3.0, ACME.asset_id): (controls.buy(3.0, "10"),),
        # Placed at 4.7, on PEER's bar: after the halt ended at 4.5, before
        # ACME's first event -- after the venue's uncross, so not in it.
        (4.7, peer.asset_id): (controls.buy(4.7, "5"),),
    }
    strategy = reopening.OnBars(script)
    base = controls.config(ACME, peer, controls=computed(published))
    cfg = replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))
    records = [
        *(record.payload for record in reopening.bars(BARS).records),
        Bar(peer.asset_id, 4.7, D("50"), D("50"), D("50"), D("50"), D("1000000"), None, None,
            TimeFrame.M1),
    ]  # fmt: skip
    dataset = MarketDataset.of("REOPEN", sorted(records, key=lambda bar: bar.timestamp))
    result = BacktestEngine.run(
        cfg, dataset, running_strategy_state("S", strategy), context_factory
    )
    # The uncross: 103 bid 50 + the run's 10 against 80 offered to 102 -- 60
    # at 102 and at 103, alike in imbalance; the highest, 103. The order of 5
    # takes its continuous turn at 5.0's close, 106.
    assert controls.trades(result) == [
        (5.0, D("10.000000"), D("103")),
        (5.0, D("5.000000"), D("106.00")),
    ]
    assert reopened(result).matched == D("60")  # nor in the volume it matched


# --------------------------------------------------------------------------- #
# Nothing crosses
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("books", "why"),
    [
        ((), "published no auction book"),
        ((book(at=2.0, bids=(("103", "50"),), offers=(("100", "40"),)),), "published no"),
        # Bids alone: the run's market order adds to them, and nothing is offered.
        ((book(bids=(("99", "50"),)),), "does not cross"),
    ],
)
def test_without_a_book_or_a_cross_nothing_crosses_and_orders_trade_on(
    books: tuple[AuctionInterest, ...], why: str
) -> None:
    script: Script = {(3.0, ACME.asset_id): (controls.buy(3.0, "10"),)}
    result, _ = reopening.run(script, computed(*books), BARS_AFTER)
    # No price is invented: the market order rests to the next event, and
    # trades there continuously, at 6.0's close.
    assert controls.trades(result) == [(6.0, D("10.000000"), D("99.00"))]
    held = reopened(result)
    assert held.price is None and why in held.reason


def test_an_uncross_outside_the_band_does_not_cross() -> None:
    published = book(bids=(("103", "50"),), offers=(("100", "40"),))
    banded = computed(
        published, bands=(PriceBand(ACME.asset_id, 1.0, None, D("90"), D("102.5"), BandAction.CAP),)
    )
    result, _ = reopening.run(
        {(3.0, ACME.asset_id): (controls.buy(3.0, "10"),)}, banded, BARS_AFTER
    )
    assert controls.trades(result) == [(6.0, D("10.000000"), D("99.00"))]
    assert "outside the band" in reopened(result).reason


def test_an_extended_auction_trades_nothing_until_it_is_held_again() -> None:
    first = book(bids=(("103", "50"),), offers=(("100", "40"),))
    later = book(at=6.2, bids=(("101", "50"),), offers=(("100", "40"),))
    banded = computed(
        first,
        later,
        extension=1.5,
        bands=(PriceBand(ACME.asset_id, 1.0, None, D("90"), D("102.5"), BandAction.CAP),),
    )
    script: Script = {
        (3.0, ACME.asset_id): (controls.buy(3.0, "10"),),
        (6.0, ACME.asset_id): (controls.buy(6.0, "3"),),
    }
    result, _ = reopening.run(script, banded, BARS_AFTER)
    # 103 is beyond the limit up at 5.0: extended to 6.5. The 6.0 bar trades
    # nothing -- not the order that waited, not one placed in the extension --
    # and at 7.0 the auction is held again from the book published at 6.2: 101,
    # within the band. Both orders waited for it, and both cross there.
    assert controls.trades(result) == [
        (7.0, D("10.000000"), D("101")),
        (7.0, D("3.000000"), D("101")),
    ]
    held = reopened(result)
    assert (held.at, held.price, held.extended_to) == (7.0, D("101"), None)


class Watching(reopening.OnBars):
    """Notes at each bar whether the strategy is told the instrument is halted."""

    def __init__(self, script: Any) -> None:
        super().__init__(script)
        self.halted: dict[float, bool] = {}

    def on_bar(self, context: Any, event: Any) -> Any:
        self.halted[event.bar.timestamp] = context.universe.halted(event.bar.asset_id)
        return super().on_bar(context, event)


def test_a_strategy_is_told_an_extended_auction_is_a_halt_until_it_is_held() -> None:
    first = book(bids=(("103", "50"),), offers=(("100", "40"),))
    banded = computed(
        first,
        extension=1.5,
        bands=(PriceBand(ACME.asset_id, 1.0, None, D("90"), D("102.5"), BandAction.CAP),),
    )
    strategy = Watching({})
    base = controls.config(ACME, controls=banded)
    cfg = replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))
    BacktestEngine.run(
        cfg, reopening.bars(BARS_AFTER), running_strategy_state("S", strategy), context_factory
    )
    # Halted at 3.0 and 4.0 by the declared halt; at 5.0 and 6.0 by the
    # extension to 6.5; trading at 7.0.
    assert strategy.halted == {2.0: False, 3.0: True, 4.0: True, 5.0: True, 6.0: True, 7.0: False}


# --------------------------------------------------------------------------- #
# Retention: the first event after the halt is recorded, not inferred
# --------------------------------------------------------------------------- #


def test_a_short_market_history_never_holds_the_auction_twice() -> None:
    peer = controls.PEER
    script: Script = {(3.0, ACME.asset_id): (controls.buy(3.0, "7", terms=limit("100")),)}
    strategy = reopening.OnBars(script)
    base = controls.config(ACME, peer, controls=reopening.halt())
    cfg = replace(
        base,
        pipeline=replace(
            base.pipeline,
            fill_timing=FillTiming.NEXT_EVENT,
            retention=RetentionPolicy(market_history=1),
        ),
    )
    records = [
        *(record.payload for record in reopening.bars(BARS_AFTER).records),
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
    # The auction at 5.0 (103) does not reach the limit at 100. PEER's bars push
    # 5.0 out of a history kept to one event; the run's record of the reopening
    # does not forget it, so 6.0 is continuous trading and its low of 99 fills
    # the limit at 100 -- where FUT-027 took 6.0 for the first event again.
    assert controls.trades(result) == [(6.0, D("7.000000"), D("100"))]
    assert reopened(result).at == 5.0


def test_every_first_event_is_recorded_though_nothing_was_working() -> None:
    result, _ = reopening.run(
        {(5.0, ACME.asset_id): (controls.buy(5.0, "3"),)}, reopening.halt(), BARS_AFTER
    )
    assert reopened(result).at == 5.0
    assert controls.trades(result) == [(6.0, D("3.000000"), D("99.00"))]


# --------------------------------------------------------------------------- #
# Persistence, replay and identity
# --------------------------------------------------------------------------- #


def _cfg(declared: TradingControls) -> Any:
    base = controls.config(ACME, controls=declared)
    return replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))


def test_a_computed_auction_resumes_to_one_result_from_every_pause() -> None:
    first = book(bids=(("103", "50"),), offers=(("100", "40"),))
    later = book(at=6.2, bids=(("101", "50"),), offers=(("100", "40"),))
    declared = computed(
        first,
        later,
        extension=1.5,
        bands=(PriceBand(ACME.asset_id, 1.0, None, D("90"), D("102.5"), BandAction.CAP),),
    )
    script: Script = {(3.0, ACME.asset_id): (controls.buy(3.0, "10"),)}
    cfg, dataset = _cfg(declared), reopening.bars(BARS_AFTER)

    def fresh() -> Any:
        return running_strategy_state("S", reopening.OnBars(script))

    whole = ReplayBacktest.run(cfg, dataset, fresh(), context_factory, schedule=RunSchedule.of())
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg, dataset, fresh(), context_factory, schedule=RunSchedule.of(),
            pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": reopening.OnBars(script)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(
            replay_from_primitives(payload), objects, dataset, RunSchedule.of()
        )
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
    assert controls.trades(whole.backtest) == [(7.0, D("10.000000"), D("101"))]


def test_a_run_holding_no_auction_writes_no_reopening() -> None:
    from alphalab.runtime.snapshot import capture

    plain, _ = controls.run({}, {ACME.asset_id: controls.flat(2.0, 3.0)}, None)
    payload = deserialize(serialize(capture(plain.state)))
    assert "reopened" not in payload["execution"]
    held, _ = reopening.run({}, reopening.halt())
    assert "reopened" in deserialize(serialize(capture(held.state)))["execution"]


def test_an_uncross_and_its_books_round_trip_and_plain_controls_keep_their_form() -> None:
    declared = computed(
        book(bids=(("103", "50"),), offers=(("100", "40"),), market_sell="5"),
        tie_break=AuctionTieBreak.LOWEST,
        priority=AuctionPriority.PRO_RATA,
        extension=30.0,
    )
    assert controls_from_primitives(controls_to_primitives(declared)) == declared
    plain = controls_to_primitives(reopening.halt())
    assert plain is not None and "auction_books" not in plain and "uncross" not in plain["halts"][0]


# --------------------------------------------------------------------------- #
# Declarations
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: TradingHalt(
            2.5, 4.5, asset_id=ACME.asset_id, uncross=AuctionUncross(AuctionTieBreak.HIGHEST)
        ),
        lambda: computed(tie_break=AuctionTieBreak.NEAREST_REFERENCE),
        lambda: AuctionUncross(AuctionTieBreak.HIGHEST, extension=0),
        lambda: AuctionUncross("HIGHEST"),  # type: ignore[arg-type]
        lambda: book(bids=(("100", "5"), ("100", "6"))),
        lambda: book(offers=(("100", "0"),)),
        lambda: book(market_buy="-1"),
        lambda: computed(book(at=3.0), book(at=3.0)),
    ],
)
def test_an_auction_declaration_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_a_published_price_off_the_instruments_grid_is_refused() -> None:
    from alphalab.instrument.registry import InstrumentRegistry, register_instruments

    gridded = replace(ACME, tick=TickSchedule.flat(D("0.05")))
    registry = register_instruments(InstrumentRegistry(), (gridded,))
    with pytest.raises(InstrumentRegistrationError):
        declare_trading_controls(registry, computed(book(bids=(("100.03", "5"),))))
