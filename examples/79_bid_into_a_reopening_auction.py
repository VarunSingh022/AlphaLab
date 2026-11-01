"""
AlphaLab Examples
=================

Example 79 : Bid into a Reopening Auction -- What the Venue Published, and When

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 70 (build a strategy)
✓ Example 77 (a short squeezed by its lender, through a halt and its reopening)

Topics
------

• A venue's published auction book and its indications -- an indicative price,
  the quantity it would match, the imbalance -- declared at their publication
  instants, and an indication withdrawn
• A strategy reading ``context.universe.auction`` while the auction is pending:
  only what was published by its instant, with its age, never the uncross
• Bidding above the indicated price to stand ahead of the published interest
  there, and the uncross the run computes from the book -- not the indication
• The venue conditions the result states it rested on, each the caller's
  declaration, and the run replayed to one result

What this shows
---------------

Phase 5's evidence for ledger FUT-040 (with FUT-033) and the engine integration
audit's EXE-014: a strategy is shown what a venue published as it published
it, and nothing the run will decide; and a result says which venue conditions
it rested on. Every price, quantity and instant below is **synthetic** -- no
venue's. (FUT-037 to FUT-039 are evidenced by their integration tests.)

Run it
------

    python examples/79_bid_into_a_reopening_auction.py
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    ExecutionMode,
    MarketDataset,
    ReplayBacktest,
    RunConfig,
    RunSchedule,
)
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.core.enums import AssetType
from alphalab.execution.policy import FillTiming
from alphalab.instrument import (
    AuctionIndication,
    AuctionInterest,
    AuctionTieBreak,
    AuctionUncross,
    PublishedAuction,
    TradingControls,
    TradingHalt,
    declare_trading_controls,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import digest_run
from alphalab.market.bar import Bar, TimeFrame
from alphalab.portfolio.account import Account
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.strategy import DiscardingLogger, FixedClock, context_factory
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, start_strategy
from alphalab.strategy.state import RuntimeState

D = Decimal
MINUTE = 60.0
T0 = 1_717_419_600.0  # 13:00 UTC on 3 June 2024
CASH = D("1000000")


def at(minutes: float) -> float:
    return T0 + minutes * MINUTE


ACME = InstrumentRecord("ACME", AssetType.EQUITY, "XSYN", "USD")
#: Halted from minute 1.5 to 4.5, reopening by an auction the venue computes
#: from its book: the price matching the most volume, ties to the highest.
HALT = TradingHalt(
    at(1.5),
    at(4.5),
    asset_id=ACME.asset_id,
    auction=True,
    uncross=AuctionUncross(AuctionTieBreak.HIGHEST),
)
#: What the venue publishes of its book during the halt.
BOOKS = (
    AuctionInterest(ACME.asset_id, at(1.7), ((D("102"), D("2000")),), ((D("100"), D("2500")),)),
    AuctionInterest(
        ACME.asset_id,
        at(3.4),
        ((D("101"), D("1000")), (D("102"), D("3000"))),
        ((D("100"), D("2500")), (D("101"), D("1000"))),
    ),
)
#: And of its indicative uncross: 100 at minute 1.8, withdrawn at 2.6 while its
#: book is revised, published again at 3.5 -- 101, 3,500 matched, 500 more to buy.
INDICATIONS = (
    AuctionIndication(ACME.asset_id, at(1.8), D("100"), D("2000"), D("-500")),
    AuctionIndication(ACME.asset_id, at(2.6), withdrawn=True),
    AuctionIndication(ACME.asset_id, at(3.5), D("101"), D("3500"), D("500")),
)
BARS = {0.0: "100", 1.0: "100", 2.0: "100", 3.0: "100", 4.0: "100", 5.0: "104", 6.0: "103"}


class AuctionBidder(BaseStrategy):
    """Watches the pending auction; bids a tick above the venue's indicated price."""

    def __init__(self) -> None:
        self.seen: dict[float, PublishedAuction | None] = {}
        self._bid = False

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        now = event.bar.timestamp
        shown = context.universe.auction(ACME.asset_id)
        self.seen[now] = shown
        indication = None if shown is None else shown.indication
        if self._bid or indication is None or indication.price is None:
            return ()
        imbalance = indication.imbalance
        if imbalance is None or imbalance <= 0:
            return ()
        # More to buy than to sell at the indicated price: buyers there are
        # rationed, published interest first. A tick above stands ahead of it.
        self._bid = True
        terms = OrderTerms.limit(indication.price + D("1"), TimeInForce.GTC)
        return (Intent("BIDDER", ACME.asset_id, D("100"), timestamp=now, terms=terms),)


CONTEXTS = context_factory(FixedClock(0.0), DiscardingLogger())


def running(strategy: AuctionBidder | None = None) -> RuntimeState:
    return start_strategy(
        create_runtime(), "BIDDER", strategy or AuctionBidder(), config={},
        subscriptions={"bars"}, at=1.0,
    )  # fmt: skip


def config() -> RunConfig:
    registry = register_instruments(InstrumentRegistry(), (ACME,))
    controls = TradingControls(halts=(HALT,), auction_books=BOOKS, indications=INDICATIONS)
    huge = D("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX79", "USD", "Example 79", 1.0),
            starting_cash=CASH,
            budget=CapitalBudget(CASH, CASH, D("0"), {"BIDDER": CASH}),
            allocation_constraints=AllocationConstraints(
                allow_shorting=False, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(huge, huge),
                position=PositionLimit(huge, huge),
                exposure=ExposureLimit(huge, huge),
                leverage=LeverageLimit(D("10")),
                margin=MarginLimit(D("1")),
                daily_loss=DailyLossLimit(huge, "UTC"),
                drawdown=DrawdownLimit(D("1")),
            ),
            instruments=declare_trading_controls(registry, controls),
            fill_timing=FillTiming.NEXT_EVENT,
        ),
        seed=79,
        start_timestamp=1.0,
    )


def data() -> MarketDataset:
    bars = [
        Bar(ACME.asset_id, at(m), D(p), D(p), D(p), D(p), D("1000000"), None, None, TimeFrame.M1)
        for m, p in BARS.items()
    ]
    return MarketDataset.of("AUCTION", bars)


def run(strategy: AuctionBidder | None = None) -> BacktestResult:
    return BacktestEngine.run(
        config(), data(), running(strategy), CONTEXTS, schedule=RunSchedule.of()
    )


def describe(shown: PublishedAuction | None) -> str:
    if shown is None:
        return "no auction pending"
    indication = shown.indication
    if indication is None:
        said = "no indication"
    elif shown.withdrawn:
        said = "indication withdrawn"
    else:
        said = (
            f"indicated {indication.price} for {indication.matched}, "
            f"imbalance {indication.imbalance}"
        )
    age = shown.book_age
    book = "no book" if age is None else f"book {age / MINUTE:.1f} min old"
    return f"{book}; {said}"


def main() -> None:
    print("Example 79 -- bid into a reopening auction")
    print()
    strategy = AuctionBidder()
    result = run(strategy)
    for when, shown in strategy.seen.items():
        print(f"  minute {(when - T0) / MINUTE:.0f}: {describe(shown)}")
    held = result.state.execution.reopened[ACME.asset_id]
    (fill,) = result.fills
    print()
    print(f"  the venue indicated 101; the run's uncross from the book: {held.price}")
    print(f"  matched {held.matched}, imbalance {held.imbalance}")
    print(f"  BIDDER bid 102 for 100 and was filled {fill.quantity:,.0f} at {fill.price}")
    print()
    print("  what the result says it rested on:")
    for condition in result.execution_assumptions.venue_conditions:
        print(f"    - {condition}")
    replay = ReplayBacktest.run(config(), data(), running(), CONTEXTS, schedule=RunSchedule.of())
    assert replay.backtest.fills == result.fills
    again = ReplayBacktest.run(config(), data(), running(), CONTEXTS, schedule=RunSchedule.of())
    assert digest_run(again.backtest).result_id == digest_run(replay.backtest).result_id
    print()
    print("  replayed: the same fills, and the same result twice")
    print(f"  {digest_run(replay.backtest).result_id[:16]}...")


if __name__ == "__main__":
    main()
