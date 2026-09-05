"""
AlphaLab Examples
=================

Example 11 : Unified Backtest

Difficulty : Intermediate

Estimated Time : 10 minutes

Topics
------

• Market datasets
• The unified backtest path
• Fill policies
• Portfolio accounting
• Deterministic, seeded runs
• Analytics

What this shows
---------------

One dataset going all the way through the real execution path:

    MarketDataset
      -> market engine        -> market event
      -> strategy             -> intents
      -> allocation           -> order requests (capital reserved)
      -> risk                 -> approve / reject
      -> OMS                  -> orders
      -> execution simulator  -> fills (per the fill policy)
      -> portfolio            -> cash, positions, realized / unrealized P&L
      -> analytics            -> performance report

There is no backtest-only order model, no backtest-only fill model, and no
second set of portfolio books: this is the same code path the rest of AlphaLab
executes through.

Run

    python examples/11_unified_backtest.py
"""

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import (
    BacktestConfig,
    BacktestEngine,
    MarketDataset,
    ReplayBacktest,
)
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.market.quote import Quote
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
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

# Core identifiers are UUID-backed, so the example mints real ones.
STRATEGY_ID = str(uuid4())
ASSET_ID = str(uuid4())

START_CASH = Decimal("100000.00")
SEED = 20220905

# Deliberately not round: prices that fall between cents are where an
# accounting model with two independent rounding points starts to drift.
MIDS = [
    Decimal("100.005"),
    Decimal("101.007"),
    Decimal("103.003"),
    Decimal("102.001"),
    Decimal("104.009"),
    Decimal("103.005"),
]


class MeanReversionDemo(BaseStrategy):
    """Buys the first quote, trims into strength, and then holds.

    Intentionally simple: the point of the example is the path a decision takes,
    not the decision itself.
    """

    def __init__(self, strategy_id: str, asset_id: str) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._plan = {
            2.0: Decimal("50"),  # open
            4.0: Decimal("-20"),  # trim
        }

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        delta = self._plan.get(event.quote.timestamp)
        if delta is None:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=delta,
                timestamp=event.quote.timestamp,
            ),
        )


# ---------------------------------------------------------------------------
# Wiring: a context factory, a running strategy, and a configuration
# ---------------------------------------------------------------------------


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def context_factory(strategy_id: str) -> StrategyContext:
    """The context each strategy sees. The pipeline does not populate it."""

    return StrategyContext(
        portfolio=object(),
        market=object(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=object(),
        config={"strategy_id": strategy_id},
        orders=object(),
        history=object(),
        universe=object(),
    )


def running_strategy() -> RuntimeState:
    """Take one strategy through its lifecycle to RUNNING."""

    state = register_strategy(
        create_runtime(), STRATEGY_ID, MeanReversionDemo(STRATEGY_ID, ASSET_ID)
    )
    strategy_state = state.strategies[STRATEGY_ID]
    strategy_state, _ = RuntimeSupervisor.configure(strategy_state, {}, 1.0)
    strategy_state, _ = RuntimeSupervisor.initialize(strategy_state, 1.1)
    strategy_state, _ = RuntimeSupervisor.subscribe(strategy_state, frozenset({"quotes"}), 1.2)
    strategy_state, _ = RuntimeSupervisor.start(strategy_state, 1.3)
    return replace(state, strategies={STRATEGY_ID: strategy_state})


def build_config() -> BacktestConfig:
    huge = Decimal("100000000")
    return BacktestConfig(
        pipeline=ExecutionPipelineConfig(
            account=Account("acct-example", "USD", "Example Account", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            # Shorting is allowed because allocation nets signed *deltas* and
            # does not see the portfolio: a "sell 20 of what I hold" intent
            # nets to -20 and would be refused as a short otherwise.
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=False
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(huge, huge),
                position=PositionLimit(huge, huge),
                exposure=ExposureLimit(huge, huge),
                leverage=LeverageLimit(Decimal("1000")),
                margin=MarginLimit(Decimal("1.00")),
                daily_loss=DailyLossLimit(huge),
                drawdown=DrawdownLimit(Decimal("1.00")),
            ),
            # A per-share commission, so the example pays real costs.
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.005"))),
        ),
        # Recording the seed is what makes this run reproducible field for
        # field -- the same orders and fills, not merely the same P&L.
        seed=SEED,
        start_timestamp=1.0,
    )


def build_dataset() -> MarketDataset:
    """One quote per second. Record ids are derived from the dataset id."""

    return MarketDataset.of(
        "EXAMPLE-DS",
        [
            Quote(
                asset_id=ASSET_ID,
                timestamp=2.0 + index,
                bid=mid - Decimal("0.005"),
                ask=mid + Decimal("0.005"),
                bid_size=Decimal("1000"),
                ask_size=Decimal("1000"),
                venue="SIM",
                currency="USD",
            )
            for index, mid in enumerate(MIDS)
        ],
    )


def main() -> None:
    """Run one dataset as a backtest, then replay it, and compare."""

    config = build_config()
    dataset = build_dataset()

    # ------------------------------------------------------------------
    # Step 1 : Backtest
    # ------------------------------------------------------------------

    result = BacktestEngine.run(config, dataset, running_strategy(), context_factory)

    # ------------------------------------------------------------------
    # Step 2 : Replay the same dataset through the same execution path
    # ------------------------------------------------------------------

    replay = ReplayBacktest.run(config, dataset, running_strategy(), context_factory)

    # ------------------------------------------------------------------
    # Step 3 : Inspect the run
    # ------------------------------------------------------------------

    valuation = result.valuation

    print("=" * 62)
    print("AlphaLab Unified Backtest Example")
    print("=" * 62)
    print()

    print(f"Dataset          : {dataset.dataset_id} ({len(dataset)} records)")
    print(f"Seed             : {result.seed}")
    print(f"Records processed: {result.records_processed}")
    print(f"Orders           : {len(result.orders)}")
    print(f"Fills            : {len(result.fills)}")
    print()

    print("Portfolio")
    print("-" * 62)
    print(f"Cash             : {valuation.cash}")
    print(f"Realized P&L     : {valuation.realized_pnl}")
    print(f"Unrealized P&L   : {valuation.unrealized_pnl}")
    print(f"Commissions      : {valuation.commission_paid}")
    print(f"Equity           : {valuation.equity}")
    print()

    # The portfolio accounting identity, exact over Decimal values.
    identity = (
        START_CASH + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )
    print(f"Accounting identity holds : {valuation.equity == identity}")
    print()

    print("Orders")
    print("-" * 62)
    for order in result.orders:
        print(
            f"  {order.side.value:<4} {order.quantity:>10} @ "
            f"{order.average_fill_price:<12} {order.status.value}"
        )
    print()

    report = result.report
    if report is not None:
        print("Analytics")
        print("-" * 62)
        print(f"Total return     : {report.returns.total_return}")
        print(f"Sharpe ratio     : {report.risk.sharpe_ratio:.4f}")
        print(f"Max drawdown     : {report.drawdowns.max_drawdown}")
        print(f"Ending capital   : {report.ending_capital}")
        print()

    # ------------------------------------------------------------------
    # Step 4 : Backtest / replay parity
    # ------------------------------------------------------------------

    replayed = replay.backtest
    print("Backtest vs replay")
    print("-" * 62)
    print(f"Replay status    : {replay.replay_status} ({replay.records_replayed} records)")
    backtest_ids = [str(o.order_id.value) for o in result.orders]
    replay_ids = [str(o.order_id.value) for o in replayed.orders]
    print(f"Order ids match  : {backtest_ids == replay_ids}")
    print(f"Valuation matches: {valuation == replayed.valuation}")
    print(f"Equity curve     : {result.equity_curve == replayed.equity_curve}")


if __name__ == "__main__":
    main()
