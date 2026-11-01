"""A venue's auction books and indications delivered during a run (phase 6, ledger FUT-043).

What FUT-040 shows a strategy from declarations made before the run, a run may
also be delivered as it happens: a :class:`DeliveredAuction` -- a book or an
indication, as of its publication, with its knowledge instant, source and
version -- kept on the market state as it arrived. The uncross reads the book,
and ``context.universe.auction`` the book and the indication, by one rule
beside what was declared: known by the instant, published since the halt began,
the latest published, a correction preferred to what it corrects. A live
session records each as known when it arrived and refuses one from the future;
a replay re-delivers the record and is shown the same. Once the auction is
held, ``context.universe.reopening`` gives its result -- never before.

ACME trades as minute bars from 2.0; the halt runs from 2.5 to 4.5, reopening
by an uncross of the venue's book; the first bar after it is at 5.0 (the
helpers are tests/integration/test_published_auctions.py's).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import PausedReplay, ReplayBacktest, RunSchedule
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.execution.policy import FillTiming
from alphalab.instrument import AuctionIndication, AuctionInterest, DeliveredAuction
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.runtime.snapshot import capture as capture_pipeline
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_published_auctions as auctions
from tests.integration import test_reopening_auctions as reopening
from tests.integration import test_trading_controls as controls
from tests.integration.harness import context_factory, running_strategy_state

D = Decimal
ACME, PEER = controls.ACME, controls.PEER
VENUE = "venue-feed"
BOOK = auctions.book(at=2.6, bids=(("101", "30"),), offers=(("100", "20"),))
LATER_BOOK = auctions.book(at=3.4, bids=(("102", "40"),), offers=(("100", "20"),))


def delivered(published: Any, known_at: float, version: str, source: str = VENUE) -> Any:
    return DeliveredAuction(published, known_at, source, version)


def indication(at: float, **kw: Any) -> AuctionIndication:
    return auctions.indication(at, **kw)


class Feedback(auctions.Watching):
    """Notes what is pending and, once held, the reopening it is told of."""

    def __init__(self, script: Any = None, follow: bool = False) -> None:
        super().__init__(script, follow)
        self.held: dict[float, Any] = {}

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.held[event.bar.timestamp] = context.universe.reopening(ACME.asset_id)
        return super().on_bar(context, event)


def run(*deliveries: Any, declared: Any = None, series: Any = None, **kw: Any) -> tuple[Any, Any]:
    from alphalab.backtesting import BacktestEngine

    strategy = Feedback(kw.pop("script", None), kw.pop("follow", False))
    base = controls.config(ACME, PEER, controls=declared or auctions.computed())
    cfg = replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))
    result = BacktestEngine.run(
        cfg, reopening.bars(series or auctions.BARS), running_strategy_state("S", strategy),
        context_factory, schedule=RunSchedule.of(auctions=deliveries),
    )  # fmt: skip
    return result, strategy


# --------------------------------------------------------------------------- #
# What is read, and from when
# --------------------------------------------------------------------------- #


def test_a_delivered_book_and_indication_are_shown_from_their_knowledge_instants() -> None:
    sent = indication(2.7, price="101", matched="20", imbalance="10")
    _, strategy = run(delivered(BOOK, 2.65, "b1"), delivered(sent, 3.2, "i1"))
    early, late = auctions.at(strategy, 3.0), auctions.at(strategy, 4.0)
    # Published at 2.7, known only at 3.2: not at 3.0.
    assert early is not None and early.book == BOOK and early.indication is None
    assert late is not None and late.indication == sent


def test_a_delivered_book_is_the_one_the_uncross_reads() -> None:
    script = {(3.0, ACME.asset_id): (controls.buy(3.0, "10"),)}
    by_delivery, _ = run(delivered(LATER_BOOK, 3.5, "b2"), script=script)
    by_declaration, _ = run(declared=auctions.computed(LATER_BOOK), script=script)
    assert controls.trades(by_delivery) == controls.trades(by_declaration) != []


def test_a_correction_replaces_what_it_corrects_from_when_it_is_known() -> None:
    first = indication(2.7, price="101", matched="20", imbalance="10")
    corrected = indication(2.7, price="100", matched="20", imbalance="5")
    _, strategy = run(delivered(first, 2.8, "v1"), delivered(corrected, 3.5, "v2"))
    assert auctions.at(strategy, 3.0).indication == first  # type: ignore[union-attr]
    assert auctions.at(strategy, 4.0).indication == corrected  # type: ignore[union-attr]


def test_a_late_older_publication_never_displaces_a_newer_one() -> None:
    newer = indication(3.0, price="102", matched="30", imbalance="0")
    older = indication(2.6, price="99", matched="5", imbalance="0")
    _, strategy = run(delivered(newer, 3.1, "n"), delivered(older, 3.6, "o"))
    assert auctions.at(strategy, 4.0).indication == newer  # type: ignore[union-attr]


def test_a_delivered_withdrawal_is_shown_withdrawn() -> None:
    sent = indication(2.7, price="101", matched="20")
    _, strategy = run(
        delivered(sent, 2.7, "1"), delivered(indication(3.3, withdrawn=True), 3.3, "2")
    )
    shown = auctions.at(strategy, 4.0)
    assert shown is not None and shown.withdrawn


def test_what_was_declared_and_what_was_delivered_are_read_by_one_rule() -> None:
    declared = auctions.computed(BOOK)
    _, strategy = run(delivered(LATER_BOOK, 3.5, "b2"), declared=declared)
    assert auctions.at(strategy, 3.0).book == BOOK  # type: ignore[union-attr]
    assert auctions.at(strategy, 4.0).book == LATER_BOOK  # type: ignore[union-attr]


def test_a_publication_from_before_the_halt_is_not_this_auctions() -> None:
    _, strategy = run(delivered(auctions.book(at=2.2, bids=(("99", "5"),)), 2.2, "early"))
    shown = auctions.at(strategy, 3.0)
    assert shown is not None and shown.book is None


def test_the_result_is_told_once_the_auction_is_held_and_never_before() -> None:
    script = {(3.0, ACME.asset_id): (controls.buy(3.0, "10"),)}
    result, strategy = run(delivered(LATER_BOOK, 3.5, "b2"), script=script)
    assert strategy.held[3.0] is None and strategy.held[4.0] is None
    held = strategy.held[5.0]
    assert held is not None and held == result.state.execution.reopened[ACME.asset_id]
    assert held.price is not None


def test_the_rule_never_reads_a_publication_known_after_the_instant() -> None:
    from alphalab.instrument.controls import published

    future = delivered(LATER_BOOK, 3.9, "b2")
    assert published(None, (future,), AuctionInterest, 2.5, 3.5) is None
    assert published(None, (future,), AuctionInterest, 2.5, 3.9) == LATER_BOOK
    # Nor reads an indication as a book, or a book as an indication.
    assert published(None, (future,), AuctionIndication, 2.5, 4.0) is None


# --------------------------------------------------------------------------- #
# Declarations refused, the record, replay
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: delivered(BOOK, 2.5, "x"),  # known before it was published
        lambda: delivered(BOOK, 2.7, "x", source=""),
        lambda: delivered(BOOK, 2.7, ""),
        lambda: DeliveredAuction("a book", 2.7, VENUE, "x"),  # type: ignore[arg-type]
    ],
)
def test_a_delivery_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_one_sources_one_version_is_delivered_once() -> None:
    with pytest.raises(RuntimeValidationError, match="delivered already"):
        run(delivered(BOOK, 2.7, "same"), delivered(LATER_BOOK, 3.5, "same"))


def test_deliveries_are_kept_as_they_arrived_and_written_only_when_made() -> None:
    result, _ = run(delivered(BOOK, 2.65, "b1"))
    payload = deserialize(serialize(capture_pipeline(result.state)))
    assert [d["version"] for d in payload["market"]["auctions"][ACME.asset_id]] == ["b1"]
    plain, _ = run()
    assert "auctions" not in deserialize(serialize(capture_pipeline(plain.state)))["market"]


def test_delivered_auctions_resume_to_one_result_from_every_pause() -> None:
    schedule = RunSchedule.of(
        auctions=(
            delivered(BOOK, 2.65, "b1"),
            delivered(indication(2.7, price="101", matched="20", imbalance="10"), 2.75, "i1"),
            delivered(LATER_BOOK, 3.5, "b2"),
        )
    )
    base = controls.config(ACME, PEER, controls=auctions.computed())
    cfg = replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))
    data = reopening.bars(auctions.BARS_AFTER)
    whole = ReplayBacktest.run(
        cfg, data, running_strategy_state("S", Feedback(follow=True)), context_factory,
        schedule=schedule,
    )  # fmt: skip
    reference = digest_run(whole.backtest).result_id
    assert controls.trades(whole.backtest) != []  # the strategy bid at what it was shown
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg, data, running_strategy_state("S", Feedback(follow=True)), context_factory,
            schedule=schedule, pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model, cfg.pipeline.simulator, {"S": Feedback(follow=True)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )  # fmt: skip
        restored = restore_replay(replay_from_primitives(payload), objects, data, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary


# --------------------------------------------------------------------------- #
# A live session, and its replay
# --------------------------------------------------------------------------- #


def live() -> tuple[Any, Any, Feedback]:
    from alphalab.broker import BrokerEngine, PaperBroker
    from alphalab.execution.costs import FREE
    from alphalab.runtime.broker_routing import RoutingConfig
    from alphalab.runtime.execution_pipeline import ExecutionRouting
    from alphalab.runtime.live import LiveSession
    from alphalab.runtime.run import ExecutionMode, RunConfig

    base = controls.config(ACME, PEER, controls=auctions.computed())
    cfg = RunConfig(
        pipeline=replace(base.pipeline, routing=ExecutionRouting.EXTERNAL),
        mode=ExecutionMode.LIVE,
        seed=7,
        start_timestamp=1.0,
        compile_analytics=False,
    )
    strategy = Feedback()
    venue = PaperBroker(FREE)
    state = LiveSession.initialize(
        cfg,
        running_strategy_state("S", strategy),
        BrokerEngine.initialize("VENUE-X", D("1000000"), "USD"),
        RoutingConfig(venue="VENUE-X", currency="USD"),
    )
    state, _ = LiveSession.connect(state, venue, 1.5)
    return state, venue, strategy


def test_a_live_session_records_a_publication_as_known_when_it_arrived() -> None:
    from alphalab.runtime.live import LiveSession

    state, venue, strategy = live()
    records = reopening.bars(auctions.BARS).records
    state, _ = LiveSession.advance(state, records[0], context_factory, venue)  # 2.0
    # Stamped known at 2.62 by the venue; the session heard it at 2.7.
    state = LiveSession.deliver_auction(state, delivered(BOOK, 2.62, "b1"), 2.7)
    (kept,) = state.run.pipeline.market.auctions[ACME.asset_id]
    assert (kept.known_at, kept.version, kept.published) == (2.7, "b1", BOOK)
    for refused in (
        delivered(LATER_BOOK, 3.5, "late"),  # known after the session's clock
        delivered(auctions.book(at=3.0, bids=(("101", "1"),)), 3.0, "future"),
    ):
        with pytest.raises(RuntimeValidationError, match="cannot have arrived"):
            LiveSession.deliver_auction(state, refused, 2.8)
    state, _ = LiveSession.advance(state, records[1], context_factory, venue)  # 3.0
    shown = auctions.at(strategy, 3.0)
    assert shown is not None and shown.book == BOOK


def test_a_replay_of_what_a_live_session_heard_shows_the_strategy_the_same() -> None:
    from alphalab.runtime.live import LiveSession

    state, venue, live_strategy = live()
    records = reopening.bars(auctions.BARS).records
    # Each arrival heard after the bar before it: the book at 2.7, after the
    # 2.0 bar; the revised book at 3.6, after the 3.0 bar.
    for record, arrivals in zip(
        records,
        (((BOOK, 2.7, "b1"),), ((LATER_BOOK, 3.6, "b2"),), (), ()),
        strict=True,
    ):
        state, _ = LiveSession.advance(state, record, context_factory, venue)
        for publication, now, version in arrivals:
            state = LiveSession.deliver_auction(
                state, delivered(publication, publication.published_at, version), now
            )
    heard = [d for items in state.run.pipeline.market.auctions.values() for d in items]
    _, replayed = run(*heard)
    for instant in (3.0, 4.0):
        assert auctions.at(replayed, instant) == auctions.at(live_strategy, instant), instant
