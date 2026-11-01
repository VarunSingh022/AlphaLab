"""
AlphaLab Examples
=================

Example 77 : A Short Squeezed by its Lender -- a Recall, a Halt, an Auction, an Amend

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 70 (build a strategy)
✓ Example 76 (a financed long-short book)

Topics
------

• A short sold against borrow a lender declares, which the lender then recalls
  -- less available from its notice, a deadline after which the excess is
  bought in
• The buy-in placed as the lender's deadline passes, through allocation, risk and
  the OMS, resting through a halt and crossing at the venue's reopening auction
• A resting bid the strategy trims with ``IntentKind.AMEND``: reduced in place,
  keeping its id and so its queue priority
• The short's borrow fee accrued day by day at a declared cutoff, on the short
  and the mark of that day, and posted once
• The run replayed, paused and resumed to the same result

What this shows
---------------

Phase 3's evidence for ledgers FUT-022, FUT-023, FUT-027 and FUT-029: a
lender's recall, a venue's auction and a strategy's amend are terms the run is
given and applies, at the market's prices, never trades it assumes. Every price,
rate and term below is **synthetic** -- no lender's, venue's or broker's.

Run it
------

    python examples/77_a_short_squeezed_by_its_lender.py
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, time
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    DailyAccrual,
    DailyBorrowFee,
    ExecutionMode,
    MarketDataset,
    PausedReplay,
    ReplayBacktest,
    RunConfig,
    RunSchedule,
    ScheduledBorrowFee,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.conventions.daycount import DayCount
from alphalab.core.enums import AssetType
from alphalab.instrument import (
    BorrowAvailability,
    BorrowTerms,
    TradingControls,
    TradingHalt,
    declare_trading_controls,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import digest_run
from alphalab.market.quote import Quote
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.account import Account
from alphalab.portfolio.types import TransactionType
from alphalab.risk.events import BuyIn
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
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy import DiscardingLogger, FixedClock, context_factory
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, start_strategy
from alphalab.strategy.state import RuntimeState

D = Decimal
HOUR = 3600.0
T0 = 1_717_419_600.0  # 13:00 UTC on 3 June 2024
CASH = D("200000")
STRATEGY_ID = "SHORT"


def at(hours: float) -> float:
    return T0 + hours * HOUR


#: 1,000 lent at 2% a year; from hour 2 the lender recalls all but 400, to be
#: covered by hour 3.
LENDER = BorrowTerms(
    (
        BorrowAvailability(at(0), D("1000"), D("0.02")),
        BorrowAvailability(at(2), D("400"), D("0.02"), buy_in_at=at(3)),
    ),
    DayCount.ACT_360,
)
LENDCO = InstrumentRecord("LENDCO", AssetType.EQUITY, "XSYN", "USD", borrow=LENDER)
#: The venue halts LENDCO from hour 3.5 to 5.5 and reopens it by auction.
CONTROLS = TradingControls(
    halts=(TradingHalt(at(3.5), at(5.5), asset_id=LENDCO.asset_id, reason="news", auction=True),)
)
#: Hourly quotes: the squeeze.
PRICES = {0: "100", 1: "99", 2: "101", 3: "102", 4: "106", 5: "107", 6: "104", 7: "103", 8: "103"}


class Short(BaseStrategy):
    """Shorts 1,000; rests a bid of 600 at 90 to take profit; trims it to 300."""

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        now = event.quote.timestamp
        if now == at(0):
            return (Intent(STRATEGY_ID, LENDCO.asset_id, D("-1000"), timestamp=now),)
        bid = OrderTerms.limit(D("90"), TimeInForce.GTC)
        if now == at(1):
            return (Intent(STRATEGY_ID, LENDCO.asset_id, D("600"), timestamp=now, terms=bid),)
        if now == at(2):
            # A smaller bid at the same price: amended in place, its place kept.
            return (
                Intent(
                    STRATEGY_ID,
                    LENDCO.asset_id,
                    D("300"),
                    timestamp=now,
                    kind=IntentKind.AMEND,
                    terms=bid,
                ),
            )
        return ()


CONTEXTS = context_factory(FixedClock(0.0), DiscardingLogger())


def running() -> RuntimeState:
    return start_strategy(
        create_runtime(), STRATEGY_ID, Short(), config={}, subscriptions={"quotes"}, at=1.0
    )


def config() -> RunConfig:
    registry = declare_trading_controls(
        register_instruments(InstrumentRegistry(), (LENDCO,)), CONTROLS
    )
    huge = D("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX77", "USD", "Example 77", 1.0),
            starting_cash=CASH,
            budget=CapitalBudget(CASH, CASH, D("0"), {STRATEGY_ID: CASH}),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=True
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
            instruments=registry,
        ),
        seed=77,
        start_timestamp=1.0,
    )


def schedule() -> RunSchedule:
    # The day's fee accrues at 20:30 UTC, on the short and the mark then.
    day = DailyAccrual("UTC", time(20, 30))
    fee = ScheduledBorrowFee(date(2024, 6, 3), date(2024, 6, 4), at(8))
    return RunSchedule.of(borrow_fees=[DailyBorrowFee(fee, day)])


def data() -> MarketDataset:
    size = D("100000")
    quotes = [
        Quote(LENDCO.asset_id, at(h), D(p), D(p), size, size, "XSYN", "USD")
        for h, p in PRICES.items()
    ]
    return MarketDataset.of("SQUEEZE", quotes)


def run() -> BacktestResult:
    return BacktestEngine.run(config(), data(), running(), CONTEXTS, schedule=schedule())


def replayed(pause: int | None = None) -> BacktestResult:
    cfg, dataset, timed = config(), data(), schedule()
    if pause is None:
        return ReplayBacktest.run(cfg, dataset, running(), CONTEXTS, schedule=timed).backtest
    paused = ReplayBacktest.run(
        cfg,
        dataset,
        running(),
        CONTEXTS,
        schedule=timed,
        pause_when=lambda progress: bool(progress.records_replayed == pause),
    )
    assert isinstance(paused, PausedReplay)
    payload = deserialize(serialize(capture_replay(paused)))
    pipeline = cfg.pipeline
    objects = RunObjects(
        RuntimeObjects(
            pipeline.sizing_model, pipeline.simulator, {STRATEGY_ID: Short()}, pipeline.instruments
        ),
        cfg.fill_policy,
    )
    restored = restore_replay(replay_from_primitives(payload), objects, dataset, timed)
    return ReplayBacktest.resume(restored, CONTEXTS).backtest


def main() -> None:
    print("Example 77 -- a short squeezed by its lender")
    print()
    result = run()
    portfolio = result.state.portfolio
    for t in portfolio.ledger.transactions:
        if t.type in (TransactionType.BUY, TransactionType.SELL):
            hour = (t.timestamp - T0) / HOUR
            print(f"  hour {hour:>3.0f}: LENDCO {t.quantity:>7,.0f} at {t.price}")
    (bought,) = [e for e in result.state.risk.events if isinstance(e, BuyIn)]
    hour = (bought.timestamp - T0) / HOUR
    print(
        f"  buy-in placed at hour {hour:.0f}: {bought.excess:,.0f} beyond the "
        f"{bought.available:,.0f} still lent; it waited through the halt and crossed at "
        "the reopening auction"
    )
    (bid,) = [o for o in result.state.oms.orders.orders() if o.is_open]
    print(f"  the bid, amended in place: {bid.quantity:,.0f} at {bid.limit_price}, its id kept")
    (fee,) = [t for t in portfolio.ledger.transactions if t.type is TransactionType.FEE]
    print(f"  the day's borrow fee on what was still short: {fee.quantity:,.2f}")
    print()
    replay = replayed()
    assert replay.fills == result.fills
    resumed = replayed(pause=5)
    assert digest_run(resumed).result_id == digest_run(replay).result_id
    print("  replay: the same fills; paused after the fifth record, from JSON, and resumed")
    print(f"  the same result {digest_run(result).result_id[:16]}...")


if __name__ == "__main__":
    main()
