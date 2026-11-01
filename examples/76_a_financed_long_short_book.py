"""
AlphaLab Examples
=================

Example 76 : A Financed Long-Short Book -- a Broker's Terms, a Venue's Rules, an Issuer's Merger

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 70 (build a strategy)
✓ Example 72 (a run's schedule)
✓ Example 75 (a derivative's life)

Topics
------

• A pair -- long LONGCO, short SHORTCO -- margined together by a declared
  scenario model, so the hedge offsets exactly as far as the scenarios say
• LONGCO bought on margin: its initial requirement spent, the cash it lacks a
  financed debit, charged a period's interest by declared tiers and day count
• SHORTCO sold only against borrow a lender declares, paying its fee each period
• A resting bid withdrawn and re-quoted with ``IntentKind.CANCEL``
• LONGCO halted for an hour by its venue: an order placed then rests and trades
  after; a price band bounds where it trades
• SHORTCO merged into SHORTNEW by its issuer: the short carried, its basis kept,
  one event saying why
• The run replayed, paused and resumed to the same result

What this shows
---------------

v4.2.0's evidence for ledgers FUT-018 to FUT-021 and EXE-012: a leveraged book's
financing, borrow, portfolio margin, venue rules and corporate actions are terms
the run is given and applies, never figures it assumes. Every price, rate and
term below is **synthetic** -- invented for the example, not any broker's,
lender's, venue's or issuer's.

Run it
------

    python examples/76_a_financed_long_short_book.py
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    ExecutionMode,
    MarketDataset,
    PausedReplay,
    ReplayBacktest,
    RunConfig,
    RunSchedule,
    ScheduledBorrowFee,
    ScheduledExchange,
    ScheduledInterest,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.conventions.daycount import DayCount
from alphalab.core.enums import AssetType
from alphalab.instrument import (
    BandAction,
    BorrowAvailability,
    BorrowTerms,
    MarginBasis,
    MarginScenario,
    MarginTerms,
    PriceBand,
    ScenarioMargin,
    TradingControls,
    TradingHalt,
    declare_margin_model,
    declare_trading_controls,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import digest_run
from alphalab.market.quote import Quote
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio import ExchangeLeg, FinancingTier, HoldingExchange
from alphalab.portfolio.account import Account
from alphalab.portfolio.events import PositionExchanged
from alphalab.portfolio.fx import NO_RATES
from alphalab.portfolio.types import TransactionType
from alphalab.risk.events import MarginCall
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
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig, margin_position
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
T0 = 1_717_500_000.0  # an instant in June 2024, for the accrual dates below
CASH = D("100000")

HALF = MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.3"))
LENDER = BorrowTerms(
    (
        BorrowAvailability(T0, D("1500"), D("0.03")),
        BorrowAvailability(T0 + 2 * HOUR, D("1500"), D("0.06")),
    ),
    DayCount.ACT_360,
)
LONG = InstrumentRecord(
    "LONGCO", AssetType.EQUITY, "XSYN", "USD", margin=replace(HALF, purchase_on_margin=True)
)
SHORT = InstrumentRecord("SHORTCO", AssetType.EQUITY, "XSYN", "USD", margin=HALF, borrow=LENDER)
SHORTNEW = InstrumentRecord("SHORTNEW", AssetType.EQUITY, "XSYN", "USD", margin=HALF, borrow=LENDER)


def at(hours: float) -> float:
    return T0 + hours * HOUR


def scenarios() -> ScenarioMargin:
    """Both names together 15% up or down, or apart by 10%: what the pair can lose."""

    def moves(long: str, short: str) -> dict[str, Decimal]:
        return {LONG.asset_id: D(long), SHORT.asset_id: D(short), SHORTNEW.asset_id: D(short)}

    return ScenarioMargin(
        (
            MarginScenario("market up", moves("0.15", "0.15")),
            MarginScenario("market down", moves("-0.15", "-0.15")),
            MarginScenario("pair apart", moves("-0.05", "0.05")),
            MarginScenario("pair together", moves("0.05", "-0.05")),
        ),
        maintenance_ratio=D("0.75"),
        floor_rate=D("0.04"),
    )


CONTROLS = TradingControls(
    bands=(PriceBand(LONG.asset_id, T0, None, D("80"), D("120"), BandAction.CAP),),
    halts=(TradingHalt(at(1.5), at(2.5), asset_id=LONG.asset_id, reason="pending news"),),
)

#: Hourly quotes. SHORTCO stops quoting at the merger; SHORTNEW starts after it.
PRICES = {
    LONG.asset_id: {0: "100", 1: "101", 2: "102", 3: "103", 4: "104"},
    SHORT.asset_id: {0: "50", 1: "50", 2: "49"},
    SHORTNEW.asset_id: {3: "98", 4: "97"},
}


class PairTrader(BaseStrategy):
    """Short 1,000 SHORTCO, then long 1,500 LONGCO on margin; re-quotes a resting bid.

    The short comes first: a short sale spends buying power, and the margin
    purchase after it leaves the cash below zero, financed.
    """

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        now, asset = event.quote.timestamp, event.quote.asset_id
        if now == at(0) and asset == SHORT.asset_id:
            return (Intent(STRATEGY_ID, SHORT.asset_id, D("-1000"), timestamp=now),)
        if now == at(1) and asset == LONG.asset_id:
            return (Intent(STRATEGY_ID, LONG.asset_id, D("1500"), timestamp=now),)
        if now == at(2) and asset == LONG.asset_id:
            # LONGCO is halted now (the context says so): a bid placed now rests.
            assert context.universe.halted(LONG.asset_id)
            bid = OrderTerms.limit(D("95"), TimeInForce.GTC)
            return (Intent(STRATEGY_ID, LONG.asset_id, D("100"), timestamp=now, terms=bid),)
        if now == at(3) and asset == LONG.asset_id:
            # The halt is over and the dip did not come: withdraw the bid and
            # re-quote it at the market -- a cancel and a new limit in one return.
            closer = OrderTerms.limit(D("103"), TimeInForce.GTC)
            return (
                Intent(STRATEGY_ID, LONG.asset_id, D("0"), timestamp=now, kind=IntentKind.CANCEL),
                Intent(STRATEGY_ID, LONG.asset_id, D("100"), timestamp=now, terms=closer),
            )
        return ()


def schedule() -> RunSchedule:
    day = (date(2024, 6, 4), date(2024, 6, 5))
    debit = (FinancingTier(D("10000"), D("0.065")), FinancingTier(None, D("0.055")))
    merger = HoldingExchange(
        SHORT.asset_id,
        (ExchangeLeg(SHORTNEW.asset_id, D("0.5"), D("1")),),
        reference="SHORTCO into SHORTNEW, 1 for 2",
    )
    return RunSchedule.of(
        exchanges=[ScheduledExchange(merger, at(2.5))],
        interest=[ScheduledInterest("USD", *day, at(4), DayCount.ACT_360, debit)],
        borrow_fees=[ScheduledBorrowFee(*day, at(4))],
    )


STRATEGY_ID = "PAIR"
CONTEXTS = context_factory(FixedClock(0.0), DiscardingLogger())


def running() -> RuntimeState:
    return start_strategy(
        create_runtime(), STRATEGY_ID, PairTrader(), config={}, subscriptions={"quotes"}, at=1.0
    )


def config() -> RunConfig:
    registry = register_instruments(InstrumentRegistry(), (LONG, SHORT, SHORTNEW))
    registry = declare_trading_controls(declare_margin_model(registry, scenarios()), CONTROLS)
    # The capital ceilings allocation sizes by are the desk's, and notional: a
    # financed book's are set to admit its leverage. Its margin is the model's.
    ceiling = CASH * 10
    huge = D("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX76", "USD", "Example 76", 1.0),
            starting_cash=CASH,
            budget=CapitalBudget(ceiling, ceiling, D("0"), {STRATEGY_ID: ceiling}),
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
        seed=76,
        start_timestamp=1.0,
    )


def quote(asset_id: str, hour: int, mid: str) -> Quote:
    """A quote with no spread: its mid is the price the run trades and marks at."""

    size = D("100000")
    return Quote(asset_id, at(hour), D(mid), D(mid), size, size, "XSYN", "USD")


def data() -> MarketDataset:
    quotes = [
        quote(asset, hour, mid) for asset, series in PRICES.items() for hour, mid in series.items()
    ]
    return MarketDataset.of(
        "FINANCED-BOOK", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id))
    )


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
            pipeline.sizing_model,
            pipeline.simulator,
            {STRATEGY_ID: PairTrader()},
            pipeline.instruments,
        ),
        cfg.fill_policy,
    )
    restored = restore_replay(replay_from_primitives(payload), objects, dataset, timed)
    return ReplayBacktest.resume(restored, CONTEXTS).backtest


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


def main() -> None:
    print("Example 76 -- a financed long-short book")
    print()
    result = run()
    state = result.state
    portfolio = state.portfolio
    for t in portfolio.ledger.transactions:
        if t.type in (TransactionType.BUY, TransactionType.SELL):
            hour = (t.timestamp - T0) / HOUR
            name = {LONG.asset_id: "LONGCO", SHORT.asset_id: "SHORTCO"}.get(t.asset_id, t.asset_id)
            print(f"  hour {hour:>4.1f}: {name:<8} {t.quantity:>7,.0f} at {t.price}")
    (merged,) = [e for e in portfolio.events if isinstance(e, PositionExchanged)]
    print(
        f"  merged: {merged.quantity:,.0f} SHORTCO became "
        f"{next(iter(merged.received.values())):,.0f} SHORTNEW"
    )
    interest = [
        t.quantity for t in portfolio.ledger.transactions if t.type is TransactionType.INTEREST
    ]
    fees = [t.quantity for t in portfolio.ledger.transactions if t.type is TransactionType.FEE]
    print(
        f"  interest on the debit: {money(sum(interest, D('0')))}; "
        f"borrow fees: {money(sum(fees, D('0')))}"
    )
    figures = margin_position(state, NO_RATES, at(4))
    assert figures is not None
    alone = sum((h.initial for h in figures.holdings), D("0"))
    print(
        f"  margin: {money(figures.initial)} under the scenarios, against {money(alone)} "
        f"leg by leg; equity {money(figures.equity)}"
    )
    calls = [e for e in state.risk.events if isinstance(e, MarginCall)]
    print(f"  margin calls: {len(calls)}")
    print()
    replay = replayed()
    assert replay.fills == result.fills
    resumed = replayed(pause=6)
    assert digest_run(resumed).result_id == digest_run(replay).result_id
    print("  replay: the same fills; paused after the sixth record, from JSON, and resumed")
    print(f"  the same result {digest_run(result).result_id[:16]}...")


if __name__ == "__main__":
    main()
