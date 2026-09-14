"""
AlphaLab Examples
=================

Example 14 : Multi-Currency Settlement, the FX Feed, and the Strategy Registry

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 11 (the unified execution path)

Topics
------

• An FX rate feed, and the three rules it applies to a quote
• Settling fills in more than one currency
• Realized P&L and commission in the currency each was earned in
• Reporting one figure in one currency, with the rates that produced it
• A strategy-class registry: identity in, executable code out
• The refusals: a missing rate, an unsettled currency, an unknown strategy

What this shows
---------------

The three capabilities v2.17 adds, driven along the paths they really share::

    FxRateSource
      -> FxFeed              -> an FxRates table, with provenance
      -> ExecutionPipeline   -> fills denominated in the instrument's currency
      -> PortfolioState      -> cash, P&L and commission per settlement currency
      -> PortfolioValuation  -> one figure in one currency, and the rates used

    StrategyDefinition
      -> StrategyClassRegistry -> the class that implements it
      -> RuntimeState          -> a running strategy
      -> ExecutionPipeline     -> the same path as every other run

Nothing here is a shortcut around the execution path, and nothing invents a
rate. Every rate is supplied by the source below, carries where it came from and
when it was true, and a figure that would need a rate nobody supplied is
**refused** rather than estimated.

Run

    python examples/14_multi_currency_settlement.py
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.core.enums import AssetType
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.market.quote import Quote
from alphalab.portfolio.account import Account
from alphalab.portfolio.exceptions import MixedCurrencyValuationError
from alphalab.portfolio.fx import FxRate
from alphalab.portfolio.fx_feed import FxFeed, SequenceFxSource
from alphalab.portfolio.valuation import PortfolioValuation
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
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineConfig
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.registry import (
    StrategyClassRegistry,
    UnknownStrategyError,
    runtime_for,
)
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from alphalab.studio.strategy import StrategyDefinition

# Two instruments, in two currencies. The registry is the authority on which:
# ``asset_id`` is derived from the four fields below, so changing the currency
# names a different instrument rather than re-denominating this one.
APPLE = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", sector="Technology")
SAP = InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR", sector="Technology")
TOYOTA = InstrumentRecord("7203", AssetType.EQUITY, "XTKS", "JPY", sector="Consumer")

START_CASH = Decimal("1000000.00")
EUR_FUNDING = Decimal("110000.00")

STRATEGY_ID = "eu-momentum-1"


# ---------------------------------------------------------------------------
# 1. A strategy, declared once and registered once
# ---------------------------------------------------------------------------


class EuropeanMomentum(BaseStrategy):
    """Buys once, trims once. The point is the path, not the decision."""

    def __init__(self, strategy_id: str, parameters: Mapping[str, float]) -> None:
        self._strategy_id = strategy_id
        self._size = Decimal(str(parameters["size"]))
        self._asset_id = str(parameters.get("asset_id", SAP.asset_id))
        # t=6.0 is the JPY quote in step 7: the strategy asks for it, and the
        # settlement boundary is what declines -- which is the point being shown.
        self._plan = {2.0: self._size, 4.0: -self._size / 2, 6.0: self._size}

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        delta = self._plan.get(event.quote.timestamp)
        if delta is None:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=event.quote.asset_id,
                target=delta,
                timestamp=event.quote.timestamp,
            ),
        )


def build_registry() -> StrategyClassRegistry:
    """One identity, one class. A duplicate registration is refused."""

    return StrategyClassRegistry().register(STRATEGY_ID, EuropeanMomentum)


def declaration() -> StrategyDefinition:
    """What a lifecycle deployment would hand the registry.

    ``StrategyDefinition`` is the canonical record of what a strategy *is*; the
    registry stores none of it, only the identity and the class.
    """

    return StrategyDefinition(
        strategy_id=STRATEGY_ID,
        name="European Momentum",
        version="1",
        author="quant",
        description="Buys SAP once and trims half.",
        parameters={"size": 10.0},
    )


# ---------------------------------------------------------------------------
# 2. An FX feed
# ---------------------------------------------------------------------------


def build_feed() -> Any:
    """Fold a source's quotes into a rate table, applying the three rules.

    The source below deliberately misbehaves the way a live one does: a quote
    arrives late and out of order, and one is redelivered after a notional
    reconnect. The fold is what makes both safe.
    """

    source = SequenceFxSource.of(
        "ECB",
        [
            FxRate("EUR", "USD", Decimal("1.08"), 1.0, "ECB"),
            FxRate("USD", "EUR", Decimal("0.925926"), 1.0, "ECB"),
            FxRate("EUR", "USD", Decimal("1.10"), 2.0, "ECB"),
            FxRate("EUR", "USD", Decimal("1.02"), 1.5, "ECB"),  # late: superseded
            FxRate("EUR", "USD", Decimal("1.10"), 2.0, "ECB"),  # redelivered: duplicate
        ],
    )
    return FxFeed.drain(FxFeed.initialize("ECB-FEED", max_age_seconds=3600.0), source)


# ---------------------------------------------------------------------------
# 3. Wiring
# ---------------------------------------------------------------------------


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def context_factory(strategy_id: str) -> StrategyContext:
    return StrategyContext(
        portfolio=NoPortfolio(),
        market=NoMarket(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=NoRiskView(),
        config={"strategy_id": strategy_id},
        orders=NoOrders(),
    )


def running(registry: StrategyClassRegistry) -> RuntimeState:
    """Build the runtime from the registry, then drive it to RUNNING.

    ``runtime_for`` registers; the lifecycle transitions are
    ``RuntimeSupervisor``'s, because ``configure`` and ``subscribe`` take
    decisions a registry has no input for.
    """

    state = runtime_for(registry, [declaration()])
    started = {}
    for strategy_id, strategy_state in state.strategies.items():
        configured, _ = RuntimeSupervisor.configure(strategy_state, {}, 1.0)
        initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
        subscribed, _ = RuntimeSupervisor.subscribe(initialized, frozenset({"quotes"}), 1.2)
        started[strategy_id], _ = RuntimeSupervisor.start(subscribed, 1.3)
    return replace(state, strategies=started)


def instruments() -> InstrumentRegistry:
    return register_instruments(InstrumentRegistry(), (APPLE, SAP, TOYOTA))


def build_config() -> ExecutionPipelineConfig:
    """A pipeline that reports in USD and may also settle in EUR.

    ``also_settles`` is empty by default, which is the single-currency pipeline
    every run had before v2.17. Naming EUR here does three things: it lets a EUR
    instrument be traded, it lets a EUR-denominated fill be booked, and it makes
    the resulting book genuinely mixed -- so every valuation of it then needs an
    ``FxRates`` table, and refuses without one.

    The budget must name its currency once two are in play: sizing compares a
    notional against a capital figure, and with two currencies that comparison
    is meaningless unless the budget says which one it is in.
    """

    huge = Decimal("100000000")
    return ExecutionPipelineConfig(
        account=Account("acct-mc", "USD", "Multi-Currency Account", 1.0),
        starting_cash=START_CASH,
        budget=CapitalBudget(
            global_capital=START_CASH,
            maximum_exposure=START_CASH * Decimal("10"),
            currency="USD",
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True, enforce_integer_quantities=False
        ),
        risk_limits=RiskLimits(
            order_size=OrderSizeLimit(Decimal("100000"), huge),
            position=PositionLimit(huge, huge),
            exposure=ExposureLimit(huge, huge),
            leverage=LeverageLimit(Decimal("1000")),
            margin=MarginLimit(Decimal("1.00")),
            daily_loss=DailyLossLimit(huge),
            drawdown=DrawdownLimit(Decimal("1.00")),
        ),
        simulator=ExecutionSimulator(),
        currency="USD",
        also_settles=frozenset({"EUR"}),
        instruments=instruments(),
    )


def quote_at(asset_id: str, timestamp: float, mid: Decimal, currency: str) -> Quote:
    return Quote(
        asset_id=asset_id,
        timestamp=timestamp,
        bid=mid,
        ask=mid,
        bid_size=Decimal("1000"),
        ask_size=Decimal("1000"),
        venue="SIM",
        currency=currency,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 68)
    print("AlphaLab Example 14")
    print("Multi-Currency Settlement, the FX Feed, and the Strategy Registry")
    print("=" * 68)

    # ---------------------------------------------------------------- #
    # Step 1 : Drain the FX feed
    # ---------------------------------------------------------------- #

    feed, decisions = build_feed()
    rates = feed.rates

    print()
    print("Step 1 : FX feed")
    for decision in decisions:
        print(f"  {decision.summary}")
        print(f"      -> {decision.outcome.name}")
    print(f"  table holds  : {[f'{b}/{q}' for b, q in feed.pairs]}")
    print(f"  observed     : {feed.observed}   applied: {feed.applied_count}")

    # ---------------------------------------------------------------- #
    # Step 2 : Build the runtime from the registry
    # ---------------------------------------------------------------- #

    registry = build_registry()
    print()
    print("Step 2 : Strategy registry")
    print(f"  registered   : {list(registry.strategy_ids)}")
    print(f"  {STRATEGY_ID} -> {registry.require(STRATEGY_ID).qualified_name}")

    try:
        registry.construct("not-registered")
    except UnknownStrategyError:
        print("  unknown identity refused rather than guessed at")

    # ---------------------------------------------------------------- #
    # Step 3 : Fund the EUR the run will settle in
    # ---------------------------------------------------------------- #

    state = ExecutionPipeline.initialize(build_config(), running(registry), 1.0)
    state, conversion = ExecutionPipeline.convert_cash(state, EUR_FUNDING, "USD", "EUR", rates, 1.5)

    print()
    print("Step 3 : Settlement funding")
    print(f"  {conversion.summary}")
    print(f"  cash         : {dict(state.portfolio.cash.balances)}")

    # ---------------------------------------------------------------- #
    # Step 4 : Trade a EUR instrument through the real execution path
    # ---------------------------------------------------------------- #

    print()
    print("Step 4 : Execution")
    for timestamp, mid in ((2.0, Decimal("100")), (3.0, Decimal("105")), (4.0, Decimal("110"))):
        result = ExecutionPipeline.process_quote(
            state, quote_at(SAP.asset_id, timestamp, mid, "EUR"), context_factory, rates=rates
        )
        state = result.state
        for report in result.execution_reports:
            print(
                f"  t={timestamp}  filled {report.fill_quantity} @ "
                f"{report.fill_price} {report.currency}"
            )

    portfolio = state.portfolio

    # ---------------------------------------------------------------- #
    # Step 5 : Settlement truth, per currency
    # ---------------------------------------------------------------- #

    print()
    print("Step 5 : Settlement (what actually happened, in the currency it happened in)")
    print(f"  currencies   : {portfolio.settlement_currencies}")
    print(f"  cash         : {dict(portfolio.cash.balances)}")
    for position in portfolio.positions.values():
        print(f"  position     : {position.quantity} @ {position.average_cost} {position.currency}")
    print(f"  realized     : {portfolio.realized_pnl}")
    print(f"  commission   : {portfolio.commission_paid}")

    # ---------------------------------------------------------------- #
    # Step 6 : Reporting truth, one currency, with its rates
    # ---------------------------------------------------------------- #

    in_usd = PortfolioValuation.snapshot(portfolio, 5.0, "USD", rates)
    in_eur = PortfolioValuation.snapshot(portfolio, 5.0, "EUR", rates)

    print()
    print("Step 6 : Reporting (one figure, and how it got there)")
    print(f"  equity (USD) : {in_usd.equity}")
    print(f"  realized     : {in_usd.realized_pnl} USD   /   {in_eur.realized_pnl} EUR")
    print(f"  rate sources : {in_usd.rate_sources}")
    for performed in in_usd.conversions:
        print(f"      {performed.summary}")

    # ---------------------------------------------------------------- #
    # Step 7 : The refusals
    # ---------------------------------------------------------------- #

    print()
    print("Step 7 : What is refused rather than estimated")

    try:
        PortfolioValuation.snapshot(portfolio, 5.0, "USD")
    except MixedCurrencyValuationError:
        print("  a mixed book with no rates is refused, not estimated")

    refused = ExecutionPipeline.process_quote(
        state, quote_at(TOYOTA.asset_id, 6.0, Decimal("2000"), "JPY"), context_factory, rates=rates
    )
    for refusal in refused.settlement_refusals:
        print(
            f"  JPY instrument dropped: settles in {refusal.instrument_currency}, not settled here"
        )

    print()
    print("Done.")


if __name__ == "__main__":
    main()
