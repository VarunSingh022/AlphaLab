"""
AlphaLab Examples
=================

Example 78 : A Tender Split Between Holders -- Prorated, Paid Late, Corrected

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 70 (build a strategy)
✓ Example 77 (a short squeezed by its lender)

Topics
------

• Two strategies holding one position, each electing in a tender on its own:
  one for cash, the other the default stock
• The issuer's proration of the oversubscribed cash alternative, the default
  taking what it does not accept
• A record instant bounding each election by the holding recorded then
• The cash paid on its payment date -- delayed by the issuer, then corrected --
  each word a ledger entry beside the original, never settled cash early
• The payment reconciled from the ledger, and the run replayed to one result

What this shows
---------------

Phase 4's evidence for ledgers FUT-030 and FUT-034 (with FUT-024 and FUT-025):
an elective action applied to each holder's part of one position, and an
issuer's later word on its payment, both as declared inputs the run applies at
their instants -- never preferences or payments it assumes. Every price, ratio
and instant below is **synthetic** -- no issuer's, venue's or broker's.

Run it
------

    python examples/78_a_tender_split_between_holders.py
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
    ElectiveExchange,
    ExecutionMode,
    MarketDataset,
    PaymentAmendment,
    ReplayBacktest,
    RunConfig,
    RunSchedule,
)
from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import digest_run
from alphalab.market.quote import Quote
from alphalab.portfolio import PaymentChange
from alphalab.portfolio.account import Account
from alphalab.portfolio.corporate_actions import ExchangeLeg, HoldingExchange
from alphalab.portfolio.settlement import entitlements
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
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, start_strategy
from alphalab.strategy.state import RuntimeState

D = Decimal
HOUR = 3600.0
T0 = 1_717_419_600.0  # 13:00 UTC on 3 June 2024
CASH = D("1000000")


def at(hours: float) -> float:
    return T0 + hours * HOUR


TARGET = InstrumentRecord("TARGETCO", AssetType.EQUITY, "XSYN", "USD")
BIDDER = InstrumentRecord("BIDDERCO", AssetType.EQUITY, "XSYN", "USD")
REFERENCE = "TARGETCO tender 2024"
#: Half a BIDDERCO share (the default), or 120 in cash, for each TARGETCO.
STOCK = HoldingExchange(TARGET.asset_id, (ExchangeLeg(BIDDER.asset_id, D("0.5"), D("1")),))
CASH_OFFER = HoldingExchange(TARGET.asset_id, cash_per_unit=D("120"))
#: Announced at hour 1, holders recorded at hour 2, elections closed at hour 3,
#: effective at hour 4; the cash is oversubscribed -- 60% accepted -- and paid
#: at hour 6.
TENDER = ElectiveExchange(
    (STOCK, CASH_OFFER),
    0,
    at(1),
    at(3),
    at(4),
    REFERENCE,
    payment_at=at(6),
    proration=((1, D("0.6")),),
    record_at=at(2),
)
#: The issuer's later word: at hour 5 the payment is delayed to hour 8; at
#: hour 7, corrected to 118 a share.
WORDS = (
    PaymentAmendment(TARGET.asset_id, at(4), "USD", at(5), PaymentChange.DELAYED, payment_at=at(8)),
    PaymentAmendment(
        TARGET.asset_id, at(4), "USD", at(7), PaymentChange.CORRECTED, per_unit=D("118")
    ),
)


class Holder(BaseStrategy):
    """Buys ``units`` of TARGETCO at hour 0; elects ``alternative`` at hour 2.5, if any."""

    def __init__(self, sid: str, units: str, alternative: int | None) -> None:
        self._sid, self._units, self._alternative = sid, D(units), alternative

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        now, asset = event.quote.timestamp, event.quote.asset_id
        if asset != TARGET.asset_id:
            return ()
        if now == at(0):
            return (Intent(self._sid, asset, self._units, timestamp=now),)
        if now == at(2.5) and self._alternative is not None:
            return (
                Intent(
                    self._sid,
                    asset,
                    D(self._alternative),
                    timestamp=now,
                    kind=IntentKind.ELECT,
                    metadata={"action": REFERENCE},
                ),
            )
        return ()


CONTEXTS = context_factory(FixedClock(0.0), DiscardingLogger())


def running() -> RuntimeState:
    state = create_runtime()
    for sid, units, alternative in (("CASHY", "100", 1), ("STOCKY", "60", None)):
        state = start_strategy(
            state, sid, Holder(sid, units, alternative), config={},
            subscriptions={"quotes"}, at=1.0,
        )  # fmt: skip
    return state


def config() -> RunConfig:
    registry = register_instruments(InstrumentRegistry(), (TARGET, BIDDER))
    huge = D("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX78", "USD", "Example 78", 1.0),
            starting_cash=CASH,
            budget=CapitalBudget(CASH, CASH, D("0"), {"CASHY": CASH, "STOCKY": CASH}),
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
            instruments=registry,
        ),
        seed=78,
        start_timestamp=1.0,
    )


def schedule() -> RunSchedule:
    return RunSchedule.of(exchanges=[TENDER], payments=WORDS)


def data() -> MarketDataset:
    size = D("100000")
    quotes = [
        Quote(TARGET.asset_id, at(h), D("100"), D("100"), size, size, "XSYN", "USD")
        for h in (0, 1, 2, 2.5, 3, 4, 5, 6, 7, 8, 9)
    ] + [
        Quote(BIDDER.asset_id, at(h), D("210"), D("210"), size, size, "XSYN", "USD")
        for h in (4, 5, 6, 7, 8, 9)
    ]
    return MarketDataset.of("TENDER", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id)))


def run() -> BacktestResult:
    return BacktestEngine.run(config(), data(), running(), CONTEXTS, schedule=schedule())


def main() -> None:
    print("Example 78 -- a tender split between holders")
    print()
    result = run()
    portfolio = result.state.portfolio
    bidder = portfolio.positions.get(BIDDER.asset_id)
    held = D("0") if bidder is None else bidder.quantity
    (declared,) = [
        e for e in entitlements(portfolio.ledger.transactions, at(4)) if e.currency == "USD"
    ]
    accepted = declared.declared / CASH_OFFER.cash_per_unit
    print(f"  CASHY elected cash for its 100; 60% accepted: {accepted:,.0f} for cash")
    print(f"  the default for CASHY's other 40 and STOCKY's 60: {held:,.0f} BIDDERCO")
    for when in (at(6.5), at(8.5)):
        (paid,) = [
            e for e in entitlements(portfolio.ledger.transactions, when) if e.currency == "USD"
        ]
        hour = (when - T0) / HOUR
        until = (
            "" if paid.settles_at is None else f" until hour {(paid.settles_at - T0) / HOUR:.0f}"
        )
        print(f"  at hour {hour:.1f}: owed {paid.owed:,.2f} USD, {paid.status.name.lower()}{until}")
    replay = ReplayBacktest.run(config(), data(), running(), CONTEXTS, schedule=schedule())
    assert replay.backtest.fills == result.fills
    again = ReplayBacktest.run(config(), data(), running(), CONTEXTS, schedule=schedule())
    assert digest_run(again.backtest).result_id == digest_run(replay.backtest).result_id
    print()
    print("  replayed: the same fills, and the same result twice")
    print(f"  {digest_run(replay.backtest).result_id[:16]}...")


if __name__ == "__main__":
    main()
