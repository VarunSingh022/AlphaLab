"""A strategy's view of what a venue publishes of an auction to come (phase 5, ledger FUT-040).

``context.universe.auction`` shows, while a reopening auction is pending -- its
halt in force, or ended and its auction not yet held, or extended -- the
:class:`AuctionInterest` and the :class:`AuctionIndication` the venue last
published since the halt began, as of the strategy's instant: a price, a matched
quantity and an imbalance only where the venue gave them, a withdrawal as a
withdrawal. Never anything published later, never the uncross the run will
compute, never depth the venue did not publish; and an indication never moves
the uncross.

ACME trades as minute bars from 2.0; the halt runs from 2.5 to 4.5, and the
first bar after it is at 5.0 (the helpers are tests/integration/test_computed_auctions.py's).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import BacktestEngine, PausedReplay, ReplayBacktest, RunSchedule
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.execution.policy import FillTiming
from alphalab.instrument import (
    AuctionIndication,
    BandAction,
    PriceBand,
    PublishedAuction,
    TradingControls,
    TradingHalt,
)
from alphalab.instrument.controls import controls_from_primitives, controls_to_primitives
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.lifecycle import digest_run
from alphalab.market.bar import Bar, TimeFrame
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import NoUniverse, StrategyContext
from alphalab.strategy.events import Intent
from tests.integration import test_computed_auctions as computed_auctions
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_reopening_auctions as reopening
from tests.integration import test_trading_controls as controls
from tests.integration.harness import context_factory, running_strategy_state

D = Decimal
ACME, PEER = controls.ACME, controls.PEER
book, computed = computed_auctions.book, computed_auctions.computed
BARS, BARS_AFTER = reopening.BARS, reopening.BARS_AFTER


def indication(
    at: float,
    price: str | None = None,
    matched: str | None = None,
    imbalance: str | None = None,
    withdrawn: bool = False,
) -> AuctionIndication:
    def value(text: str | None) -> Decimal | None:
        return None if text is None else D(text)

    return AuctionIndication(
        ACME.asset_id, at, value(price), value(matched), value(imbalance), withdrawn
    )


class Watching(reopening.OnBars):
    """Notes what ``context.universe.auction`` shows at each bar; trades its script."""

    def __init__(self, script: Any = None, follow: bool = False) -> None:
        super().__init__(script or {})
        self.shown: dict[tuple[float, str], PublishedAuction | None] = {}
        self._follow = follow

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        at, asset = event.bar.timestamp, event.bar.asset_id
        shown = context.universe.auction(ACME.asset_id)
        self.shown[(at, asset)] = shown
        scripted = tuple(super().on_bar(context, event))
        if self._follow and asset == ACME.asset_id and shown is not None:
            published = None if shown.indication is None else shown.indication.price
            if published is not None:
                # Bid at the indicative price: what the strategy saw decides the run.
                return (*scripted, controls.buy(at, "5", terms=reopening.limit(str(published))))
        return scripted


def run(declared: TradingControls, series: Any = BARS, **kw: Any) -> tuple[Any, Watching]:
    strategy = Watching(kw.pop("script", None), kw.pop("follow", False))
    base = controls.config(ACME, PEER, controls=declared)
    cfg = replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))
    data = kw.pop("data", None) or reopening.bars(series)
    result = BacktestEngine.run(
        cfg, data, running_strategy_state("S", strategy), context_factory, schedule=RunSchedule.of()
    )
    return result, strategy


def at(strategy: Watching, instant: float, asset: Any = ACME) -> PublishedAuction | None:
    return strategy.shown[(instant, asset.asset_id)]


def bars_with_peer(series: dict[float, tuple[str, str]], peer_at: float) -> MarketDataset:
    """ACME's bars and one of PEER at ``peer_at``: an event between two of ACME's."""

    bars = [
        Bar(asset.asset_id, instant, D(o), max(D(o), D(c)), min(D(o), D(c)), D(c), D("1000000"),
            None, None, TimeFrame.M1)
        for asset, instant, (o, c) in (
            *((ACME, instant, prices) for instant, prices in series.items()),
            (PEER, peer_at, ("50", "50")),
        )
    ]  # fmt: skip
    return MarketDataset.of("PENDING", sorted(bars, key=lambda b: (b.timestamp, b.asset_id)))


PUBLISHED = (
    book(at=2.6, bids=(("101", "30"),), offers=(("100", "20"),)),
    book(at=3.5, bids=(("102", "40"),), offers=(("100", "20"),)),
)
INDICATED = (
    indication(2.7, price="101", matched="20", imbalance="10"),
    indication(3.6, price="102", matched="20", imbalance="20"),
)


# --------------------------------------------------------------------------- #
# What is shown, and when
# --------------------------------------------------------------------------- #


def test_the_book_and_indication_published_by_the_strategys_instant_are_shown() -> None:
    _, strategy = run(computed(*PUBLISHED, indications=INDICATED))
    assert at(strategy, 2.0) is None  # before the halt
    shown = at(strategy, 3.0)
    assert shown is not None and shown.since == 2.5
    assert (shown.book, shown.indication) == (PUBLISHED[0], INDICATED[0])
    assert shown.book_age == pytest.approx(0.4) and shown.indication_age == pytest.approx(0.3)
    later = at(strategy, 4.0)
    assert later is not None and (later.book, later.indication) == (PUBLISHED[1], INDICATED[1])
    # The auction is held at 5.0, before the strategy sees the bar: nothing pending.
    assert at(strategy, 5.0) is None


def test_nothing_published_after_the_strategys_instant_is_shown() -> None:
    late = (indication(3.9, price="150", matched="1", imbalance="0"),)
    _, strategy = run(computed(book(at=3.95, bids=(("150", "1"),)), indications=late))
    shown = at(strategy, 3.0)
    assert shown is not None and shown.book is None and shown.indication is None


def test_what_was_published_before_the_halt_is_not_this_auctions() -> None:
    early = (indication(2.2, price="99", matched="5", imbalance="0"),)
    _, strategy = run(computed(book(at=2.2, bids=(("99", "5"),)), indications=early))
    shown = at(strategy, 3.0)
    assert shown is not None and shown.book is None and shown.indication is None


def test_a_withdrawn_indication_is_shown_withdrawn() -> None:
    withdrawn = (indication(2.7, price="101", matched="20"), indication(3.5, withdrawn=True))
    _, strategy = run(computed(*PUBLISHED, indications=withdrawn))
    before, after = at(strategy, 3.0), at(strategy, 4.0)
    assert before is not None and not before.withdrawn
    assert after is not None and after.withdrawn and after.indication is not None
    assert after.indication.price is None


def test_what_the_venue_did_not_publish_is_not_filled_in() -> None:
    sparse = (indication(2.7, imbalance="-15"),)
    _, strategy = run(computed(book(at=2.6, market_buy="25"), indications=sparse))
    shown = at(strategy, 3.0)
    assert shown is not None and shown.indication is not None and shown.book is not None
    assert (shown.indication.price, shown.indication.matched) == (None, None)
    assert shown.indication.imbalance == D("-15")
    assert (shown.book.bids, shown.book.offers, shown.book.market_buy) == ((), (), D("25"))


def test_outside_a_pending_auction_nothing_is_shown() -> None:
    # PEER's halt reopens by auction; ACME's, published for, does not.
    plain = TradingControls(
        halts=(
            TradingHalt(2.5, 4.5, asset_id=ACME.asset_id),
            TradingHalt(2.5, 4.5, asset_id=PEER.asset_id, auction=True),
        ),
        indications=INDICATED,
    )
    _, strategy = run(plain)
    assert all(shown is None for shown in strategy.shown.values())
    _, none = run(TradingControls())
    assert all(shown is None for shown in none.shown.values())
    assert NoUniverse().auction(ACME.asset_id) is None


def test_an_auction_ended_and_not_yet_held_is_pending() -> None:
    # PEER trades at 4.7: ACME's halt has ended, and its auction is held at
    # ACME's first event after it, 5.0. Until then it is pending.
    _, strategy = run(computed(*PUBLISHED, indications=INDICATED), data=bars_with_peer(BARS, 4.7))
    shown = at(strategy, 4.7, PEER)
    assert shown is not None and shown.indication == INDICATED[1] and shown.extended_to is None


def test_an_extended_auction_is_pending_until_it_is_held_again() -> None:
    first = book(bids=(("103", "50"),), offers=(("100", "40"),))
    later = book(at=6.2, bids=(("101", "50"),), offers=(("100", "40"),))
    band = PriceBand(ACME.asset_id, 1.0, None, D("90"), D("102.5"), BandAction.CAP)
    extended = computed(first, later, extension=1.5, bands=(band,), indications=INDICATED)
    # PEER trades at 6.7: the extension's 6.5 has passed, and ACME's auction is
    # held again only at its next bar, 7.0. Until then it is pending.
    _, strategy = run(extended, data=bars_with_peer(BARS_AFTER, 6.7))
    shown = at(strategy, 6.0)
    assert shown is not None and shown.extended_to == 6.5 and shown.book == first
    after = at(strategy, 6.7, PEER)
    assert after is not None and after.extended_to == 6.5 and after.book == later
    assert at(strategy, 7.0) is None


# --------------------------------------------------------------------------- #
# No look-ahead into the uncross; an indication never moves it
# --------------------------------------------------------------------------- #


def test_an_indication_never_moves_the_uncross() -> None:
    script = {(3.0, ACME.asset_id): (controls.buy(3.0, "10"),)}
    misleading = (indication(2.7, price="90", matched="1", imbalance="-500"),)
    with_it, strategy = run(computed(*PUBLISHED, indications=misleading), script=script)
    without, _ = run(computed(*PUBLISHED), script=script)
    assert controls.trades(with_it) == controls.trades(without) != []
    # The strategy saw the venue's 90, never the run's uncross.
    shown = at(strategy, 4.0)
    assert shown is not None and shown.indication is not None and shown.indication.price == D("90")
    assert not hasattr(shown, "price")


# --------------------------------------------------------------------------- #
# Declarations, the record and replay
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: indication(3.0),
        lambda: indication(3.0, price="101", withdrawn=True),
        lambda: indication(3.0, price="0"),
        lambda: indication(3.0, matched="-1"),
        lambda: indication(3.0, imbalance="NaN"),
        lambda: AuctionIndication("", 3.0, imbalance=D("1")),
        lambda: TradingControls(
            indications=(indication(3.0, price="1"), indication(3.0, price="2"))
        ),
    ],
)
def test_an_indication_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_indications_round_trip_and_controls_without_them_keep_their_form() -> None:
    declared = computed(*PUBLISHED, indications=(*INDICATED, indication(3.8, withdrawn=True)))
    payload = deserialize(serialize(controls_to_primitives(declared)))
    assert controls_from_primitives(payload) == declared
    assert "indications" not in (controls_to_primitives(computed(*PUBLISHED)) or {})
    base = controls.config(ACME, controls=declared)
    stated = execution_assumptions(base.pipeline, base.fill_policy).venue_conditions
    assert any("published auction indications for 1 instrument (3" in line for line in stated)


def test_what_a_strategy_was_shown_resumes_to_one_result_from_every_pause() -> None:
    declared = computed(*PUBLISHED, indications=INDICATED)
    base = controls.config(ACME, PEER, controls=declared)
    cfg = replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))
    data, schedule = reopening.bars(BARS_AFTER), RunSchedule.of()
    whole = ReplayBacktest.run(
        cfg, data, running_strategy_state("S", Watching(follow=True)), context_factory,
        schedule=schedule,
    )  # fmt: skip
    reference = digest_run(whole.backtest).result_id
    # The strategy bid at what it was shown: 101 at 3.0, 102 at 4.0.
    assert {p for _, _, p in controls.trades(whole.backtest)} != set()
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg, data, running_strategy_state("S", Watching(follow=True)), context_factory,
            schedule=schedule, pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model, cfg.pipeline.simulator, {"S": Watching(follow=True)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )  # fmt: skip
        restored = restore_replay(replay_from_primitives(payload), objects, data, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
