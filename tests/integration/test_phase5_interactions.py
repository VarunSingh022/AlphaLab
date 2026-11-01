"""The phase 5 capabilities against each other (FUT-037 to FUT-040).

Each test drives several of them through one run on the canonical path: an
elective action's terms corrected after its announcement, its stock delivered
later and the delivery delayed by the issuer, the units owed never sold before
they arrive; and a book of written puts margined from a vendor's surface feed
while their underlying is halted for a reopening auction -- the strategy shown,
at one instant, the surface known and the auction published by then, and no
later one. Each is resumed from every pause to one result, every input applied
once.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.backtesting import (
    DeliveryAmendment,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.backtesting.schedule import TermsCorrection
from alphalab.instrument import (
    AuctionIndication,
    AuctionInterest,
    AuctionTieBreak,
    AuctionUncross,
    TradingControls,
    TradingHalt,
    declare_trading_controls,
)
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import HoldingExchange
from alphalab.portfolio.events import DeliveryAmended
from alphalab.portfolio.settlement import PaymentChange
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration import test_elective_exchanges as elective
from tests.integration import test_holding_exchanges as exchanges
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_split_elections as split
from tests.integration import test_surface_feeds as feeds
from tests.integration.harness import context_factory, running_strategy_state

D = Decimal
OLD, NEW = exchanges.OLD, exchanges.NEW


def resumes_to_one_result(cfg: Any, data: Any, strategies: Any, schedule: RunSchedule) -> Any:
    """The uninterrupted replay, after checking a pause at every boundary resumes to it."""

    whole = ReplayBacktest.run(cfg, data, strategies(), context_factory, schedule=schedule)
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg, data, strategies(), context_factory, schedule=schedule,
            pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        instances = {sid: entry.instance for sid, entry in strategies().strategies.items()}
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model, cfg.pipeline.simulator, instances,
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )  # fmt: skip
        restored = restore_replay(replay_from_primitives(payload), objects, data, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
    return whole.backtest


# --------------------------------------------------------------------------- #
# A corrected action, its stock delivered late, the delivery delayed
# --------------------------------------------------------------------------- #

#: The cash offer raised to 130 at 2.4; the stock delivered at 3.08, the
#: issuer's word at 3.05 delaying it to 3.5.
RAISED = TermsCorrection(
    2.4, alternatives=(elective.STOCK, HoldingExchange(OLD.asset_id, cash_per_unit=D("130")))
)
LATE = DeliveryAmendment(OLD.asset_id, 3.0, NEW.asset_id, 3.05, PaymentChange.DELAYED, 3.5)
MIDS = {OLD.asset_id: split.MIDS[OLD.asset_id], NEW.asset_id: {3.1: "200", 3.6: "200"}}


def holders() -> Any:
    # A elects cash at 2.3, under the original terms, and is kept through the
    # correction; B takes the default stock and tries to sell 10 before and
    # after it arrives.
    sells = {(3.1, NEW.asset_id): D("-10"), (3.6, NEW.asset_id): D("-10")}
    return elective.strategies(
        ("A", split.Holder(split.buys("60"), {2.3: (1, None)})),
        ("B", split.Holder({**split.buys("40"), **sells}, {})),
    )


def corrected_and_delayed() -> tuple[Any, Any, RunSchedule]:
    action = elective.action(corrections=(RAISED,), deliveries=((NEW.asset_id, 3.08),))
    return (
        exchanges.config(OLD, NEW),
        margin.prices(MIDS),
        RunSchedule.of(exchanges=[action], payments=[LATE]),
    )


def test_a_corrected_action_delivered_late_and_delayed_is_never_sold_before_it_arrives() -> None:
    cfg, data, schedule = corrected_and_delayed()
    result = resumes_to_one_result(cfg, data, holders, schedule)
    # A's 60 take the corrected cash: 60 x 130. B's 40 take 20 NEWCO, owed
    # until 3.5: its sale at 3.1 is refused, its sale of 10 at 200 at 3.6 trades.
    bought, sold = D("10000"), D("2000")
    assert exchanges.usd(result) - (exchanges.START - bought) - sold == D("7800")
    assert [q for at, q in margin.fills(result, NEW)] == [D("-10")]
    assert [at for at, _ in margin.fills(result, NEW)] == [3.6]
    (amended,) = [e for e in result.state.portfolio.events if isinstance(e, DeliveryAmended)]
    assert (amended.change, amended.settles_at) == ("DELAYED", 3.5)
    assert split.new_held(result) == D("10")
    refused = result.state.strategy.strategies["B"].instance.rejections
    assert any("pending delivery" in reason for reason in refused)


# --------------------------------------------------------------------------- #
# A surface feed and a reopening auction, seen at one instant
# --------------------------------------------------------------------------- #

#: ACME's share halted from T0 + 30 to T0 + 90, reopening by an uncross of the
#: venue's book; its book published at T0 + 40, its indication at T0 + 50.
T0 = feeds.T0
HALTED = TradingControls(
    halts=(
        TradingHalt(T0 + 30, T0 + 90, asset_id=feeds.SHARE.asset_id, auction=True,
                    uncross=AuctionUncross(AuctionTieBreak.HIGHEST)),
    ),
    auction_books=(
        AuctionInterest(feeds.SHARE.asset_id, T0 + 40, ((D("100"), D("500")),),
                        ((D("100"), D("500")),)),
    ),
    indications=(AuctionIndication(feeds.SHARE.asset_id, T0 + 50, D("100"), D("500"), D("0")),),
)  # fmt: skip
#: The vendor's surface at 30% known at T0 + 30, revised to 40% at T0 + 70.
SURFACES = (
    feeds.delivered("0.30", T0, T0 + 30, "v1"),
    feeds.delivered("0.40", T0 + 60, T0 + 70, "v2"),
)


class Watching(feeds.Watcher):
    """The surface feeds' writer of puts, noting the auction it is shown too."""

    def __init__(self) -> None:
        super().__init__()
        self.auctions: dict[float, Any] = {}

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.asset_id == feeds.SHARE.asset_id:
            self.auctions[event.quote.timestamp] = context.universe.auction(feeds.SHARE.asset_id)
        return super().on_quote(context, event)


def halted_config() -> Any:
    cfg = feeds.config(feeds.model())
    registry = declare_trading_controls(cfg.pipeline.instruments, HALTED)
    return replace(cfg, pipeline=replace(cfg.pipeline, instruments=registry))


def test_a_surface_and_an_auction_are_seen_as_known_at_one_instant_and_margin_follows() -> None:
    watchers: list[Watching] = []

    def strategies() -> Any:
        watchers.append(Watching())
        return running_strategy_state("S", watchers[-1])

    schedule = RunSchedule.of(surfaces=SURFACES)
    result = resumes_to_one_result(halted_config(), feeds.dataset(), strategies, schedule)
    watcher = watchers[0]
    # At T0 + 60, in the halt: the surface known since T0 + 30 and the auction
    # published by then -- not the revision known at T0 + 70.
    assert watcher.seen[T0 + 60] == "v1"
    shown = watcher.auctions[T0 + 60]
    assert shown is not None and shown.indication is not None
    assert (shown.book.published_at, shown.indication.published_at) == (T0 + 40, T0 + 50)
    # At T0 + 120 the auction is held and the revision is read, by the
    # strategy and by the margin check alike.
    assert watcher.auctions[T0 + 120] is None and watcher.seen[T0 + 120] == "v2"
    assert feeds.required(result, T0 + 60) == feeds.at_vol(0.30)
    known_early = feeds.run(feeds.delivered("0.40", T0, T0, "v2"))
    assert feeds.required(result, T0 + 120) == feeds.required(known_early, T0 + 120)
    held = result.state.execution.reopened[feeds.SHARE.asset_id]
    assert held.halt_end == T0 + 90
