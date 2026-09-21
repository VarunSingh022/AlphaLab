"""
AlphaLab Examples
=================

Example 44 : Expected vs Paper vs Live

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 11 (unified backtest)
✓ Example 25 (execution simulation)
✓ Example 43 (runtime health)

Topics
------

• One backtest, one paper run, one venue's own records -- compared
• Alignment declared rather than guessed
• Tolerances stated, because equality is a policy nobody chose
• Money compared per currency and never summed across two
• A venue's unmeasured slippage staying missing instead of becoming zero
• Locating a divergence: which pair disagrees tells you where to look

What this shows
---------------

On the day a strategy goes live, the question is whether it is doing what it was
supposed to do. A backtest produces trades, fills, P&L and exposure; so does a
paper run; so does a live account. All three were readable before v3.5 and none
of them was comparable, because nothing said which backtested fill corresponds
to which live one, or how far apart two numbers may be before the difference
means something.

The backtest and the paper run below go through the *same* canonical step --
`RunEngine.advance` over `ExecutionPipeline` -- which is why they agree
exactly. That parity is structural (ADR-0010), and this is what it looks like
when it is measured rather than asserted.

Run

    python examples/44_expected_paper_live_comparison.py
"""

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import BacktestEngine, ExecutionMode, MarketDataset, RunConfig
from alphalab.backtesting.state import BacktestResult
from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.ids import id_scope
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import OrderStatus, OrderType
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import FixedSlippage
from alphalab.lifecycle import (
    AlignmentKey,
    ComparisonMetric,
    ComparisonOutcome,
    ComparisonSource,
    PairComparison,
    Tolerance,
    compare_expected_paper_live,
    compare_runs,
    observations_from_backtest,
    observations_from_broker,
)
from alphalab.market.quote import Quote
from alphalab.portfolio.account import Account
from alphalab.portfolio.amounts import CurrencyAmounts
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
from alphalab.runtime.run import RunEngine
from alphalab.runtime.session import TradingSession
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

STRATEGY_ID = str(uuid4())
ASSET_ID = str(uuid4())
START_CASH = Decimal("1000000.00")
SEED = 440_000

MIDS = [
    Decimal("100.005"),
    Decimal("101.007"),
    Decimal("103.003"),
    Decimal("102.001"),
    Decimal("104.009"),
]

#: Every metric that will be compared, and how close counts. There is no
#: default: a hidden ``== 0`` calls a one-cent difference a break, and a hidden
#: ``0.01`` says a cent is fine for a position count. Both are policies nobody
#: chose, so a metric with no entry here is reported NOT_COMPARABLE.
TOLERANCES = {
    ComparisonMetric.TRADE_QUANTITY: Tolerance(absolute=Decimal("0")),
    ComparisonMetric.TRADE_PRICE: Tolerance(absolute=Decimal("0.01")),
    ComparisonMetric.FILL_QUANTITY: Tolerance(absolute=Decimal("0")),
    ComparisonMetric.FILL_PRICE: Tolerance(absolute=Decimal("0.01")),
    ComparisonMetric.SLIPPAGE: Tolerance(absolute=Decimal("0.005")),
    ComparisonMetric.EXECUTION_LATENCY: Tolerance(absolute=Decimal("0.5")),
    ComparisonMetric.REALIZED_PNL: Tolerance(absolute=Decimal("1.00")),
    ComparisonMetric.EXPOSURE: Tolerance(relative=Decimal("0.001")),
}


class ScriptedStrategy(BaseStrategy):
    """Opens on the second quote and trims on the fourth."""

    def __init__(self) -> None:
        self._plan = {3.0: Decimal("400"), 5.0: Decimal("-150")}

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        delta = self._plan.get(event.quote.timestamp)
        if delta is None:
            return ()
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=ASSET_ID,
                target=delta,
                timestamp=event.quote.timestamp,
            ),
        )


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


def running_strategy() -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, ScriptedStrategy())
    strategy = state.strategies[STRATEGY_ID]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(strategy, frozenset({"quotes"}), 1.2)
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return replace(state, strategies={STRATEGY_ID: strategy})


def run_config() -> RunConfig:
    limits = RiskLimits(
        order_size=OrderSizeLimit(Decimal("100000"), Decimal("100000000")),
        position=PositionLimit(Decimal("100000"), Decimal("100000000")),
        exposure=ExposureLimit(Decimal("100000000"), Decimal("100000000")),
        leverage=LeverageLimit(Decimal("1000")),
        margin=MarginLimit(Decimal("1.00")),
        daily_loss=DailyLossLimit(Decimal("100000000")),
        drawdown=DrawdownLimit(Decimal("1.00")),
    )
    return RunConfig(
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-44", "USD", "Example 44", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH * Decimal("10"),
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=False
            ),
            risk_limits=limits,
            # A stated cost model, so the backtest's fills carry a slippage a
            # venue's records will not have -- which is the point of section 4.
            simulator=ExecutionSimulator(slippage_model=FixedSlippage(Decimal("0.01"))),
        ),
        mode=ExecutionMode.BACKTEST,
        seed=SEED,
        start_timestamp=1.0,
    )


def dataset() -> MarketDataset:
    return MarketDataset.of(
        "EXAMPLE-44",
        [
            Quote(
                asset_id=ASSET_ID,
                timestamp=2.0 + index,
                bid=mid,
                ask=mid,
                bid_size=Decimal("100000"),
                ask_size=Decimal("100000"),
                venue="XNYS",
                currency="USD",
            )
            for index, mid in enumerate(MIDS)
        ],
    )


def paper_run(
    market: MarketDataset, config: RunConfig, strategy_state: RuntimeState
) -> BacktestResult:
    """The paper driver over the same records -- the same canonical step.

    Two things make ``AlignmentKey.ORDER_ID`` usable here, and both are
    ADR-0022's identifier contract rather than a convenience:

    1. ``id_scope(SEED)`` installs the seeded deterministic source, so a run's
       identifiers are a function of the seed rather than of ``uuid4``.
    2. The strategy state and the config are built **outside** that scope,
       exactly as ``BacktestEngine.run`` builds them at its call site. The
       stream position depends on everything drawn before it, so registering a
       strategy inside the scope would consume identifiers the backtest did not
       and every order id would be offset by that many draws.

    Get either wrong and the two runs still reproduce the same *economics* --
    the same fills at the same prices -- while sharing no identifier at all.
    The comparison then reports every record missing on both sides, which is
    the honest answer to a mis-declared alignment and is what
    ``AlignmentKey.ASSET_AND_TIME`` exists for.
    """

    with id_scope(SEED):
        state = replace(
            TradingSession.initialize(config, strategy_state),
            source_id=market.dataset_id,
        )
        for record in market.records:
            state, _ = TradingSession.advance(state, record, context_factory)
        return BacktestResult(RunEngine.finalize(state))


def venue_records(expected: BacktestResult) -> BrokerState:
    """What the venue's own records say, as a normalized ``BrokerState``.

    A deterministic local fixture standing in for an adapter's output. Every
    fill here is one cent worse than the backtest's, which is what a real venue
    looks like -- and the venue reports a *price*, never a difference from a
    reference it never saw, so no slippage is recorded.
    """

    orders: dict[str, BrokerOrder] = {}
    executions: dict[str, BrokerExecution] = {}
    # The side comes from the OMS order the fill names. An ExecutionReport's
    # quantity is always positive -- the direction lives on the order -- so
    # inferring a side from its sign would make every sell look like a buy.
    sides = {str(order.order_id.value): order.side for order in expected.orders}
    for index, report in enumerate(expected.state.execution.history.to_tuple()):
        broker_order_id = f"V-{index:03d}"
        orders[broker_order_id] = BrokerOrder(
            broker_order_id=broker_order_id,
            oms_order_id=report.order_id,
            symbol=report.asset_id,
            side=sides[report.order_id],
            order_type=OrderType.MARKET,
            quantity=abs(report.fill_quantity),
            price=report.fill_price,
            filled_quantity=abs(report.fill_quantity),
            average_fill_price=report.fill_price + Decimal("0.01"),
            status=OrderStatus.FILLED,
            created_at=report.timestamp,
            updated_at=report.timestamp,
        )
        executions[f"VX-{index:03d}"] = BrokerExecution(
            execution_id=f"VX-{index:03d}",
            broker_order_id=broker_order_id,
            symbol=report.asset_id,
            fill_quantity=report.fill_quantity,
            fill_price=report.fill_price + Decimal("0.01"),
            commission=report.commission,
            timestamp=report.timestamp,
        )
    return BrokerState(
        broker_name="EXAMPLE-VENUE",
        connection_status=ConnectionStatus.CONNECTED,
        account=BrokerAccount(
            "ACC-44", START_CASH, START_CASH, START_CASH, Decimal("0"), START_CASH, "USD"
        ),
        orders=PersistentMap(orders),
        executions=PersistentMap(executions),
    )


def exposure_of(result: BacktestResult) -> CurrencyAmounts:
    """Gross market value per settlement currency, built by the caller.

    The comparison layer computes no exposure of its own: what a book means by
    exposure depends on whether it holds shares or contracts, and a third
    authority here would be the one that forgot the multiplier.
    """

    amounts = CurrencyAmounts()
    for position in result.state.portfolio.positions.values():
        amounts = amounts.add(abs(position.market_value), position.currency)
    return amounts


def submitted_at(result: BacktestResult) -> dict[str, float]:
    return {str(order.order_id.value): order.created_at for order in result.orders}


def summarise(label: str, comparison: PairComparison) -> None:
    counts: dict[str, int] = {}
    for entry in comparison.entries:
        counts[entry.outcome.name] = counts.get(entry.outcome.name, 0) + 1
    rendered = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    print(f"  {label:<22} agrees={comparison.agrees!s:<5} {rendered}")


def main() -> None:
    print("=" * 68)
    print("AlphaLab Example 44 : Expected vs Paper vs Live")
    print("=" * 68)

    market = dataset()

    # ----------------------------------------------------------------- #
    # 1. Three sources
    # ----------------------------------------------------------------- #

    print("\n[1] Three runs over one set of records")
    expected = BacktestEngine.run(run_config(), market, running_strategy(), context_factory)
    paper = paper_run(market, run_config(), running_strategy())
    venue = venue_records(expected)
    print(
        f"  expected : {len(expected.fills)} fills, "
        f"realized {expected.state.portfolio.realized_pnl.of('USD')} USD"
    )
    print(
        f"  paper    : {len(paper.fills)} fills, "
        f"realized {paper.state.portfolio.realized_pnl.of('USD')} USD"
    )
    print(f"  live     : {len(venue.executions)} venue fills reported")

    expected_view = observations_from_backtest(
        expected,
        ComparisonSource.EXPECTED,
        AlignmentKey.ORDER_ID,
        exposure=exposure_of(expected),
        submitted_at=submitted_at(expected),
    )
    paper_view = observations_from_backtest(
        paper,
        ComparisonSource.PAPER,
        AlignmentKey.ORDER_ID,
        exposure=exposure_of(paper),
        submitted_at=submitted_at(paper),
    )
    live_view = observations_from_broker(
        venue,
        ComparisonSource.LIVE,
        AlignmentKey.ORDER_ID,
        realized_pnl=expected.state.portfolio.realized_pnl,
        exposure=exposure_of(expected),
        submitted_at=submitted_at(expected),
    )

    # ----------------------------------------------------------------- #
    # 2. Expected against paper
    # ----------------------------------------------------------------- #

    print("\n[2] Expected vs paper")
    expected_vs_paper = compare_runs(expected_view, paper_view, TOLERANCES)
    summarise("expected vs paper", expected_vs_paper)
    print("  (They agree exactly. Both went through the same canonical step,")
    print("   so parity is structural rather than lucky -- ADR-0010.)")

    # ----------------------------------------------------------------- #
    # 3. Expected against live
    # ----------------------------------------------------------------- #

    print("\n[3] Expected vs live")
    expected_vs_live = compare_runs(expected_view, live_view, TOLERANCES)
    summarise("expected vs live", expected_vs_live)
    for entry in expected_vs_live.entries_for(ComparisonMetric.FILL_PRICE):
        print(
            f"    fill price  {entry.key[:8]}...  expected={entry.expected} "
            f"observed={entry.observed}  {entry.outcome.name}"
        )

    # ----------------------------------------------------------------- #
    # 4. What a venue does not measure
    # ----------------------------------------------------------------- #

    print("\n[4] Slippage")
    for entry in expected_vs_live.entries_for(ComparisonMetric.SLIPPAGE):
        print(f"    expected={entry.expected}  observed={entry.observed}  {entry.outcome.name}")
    print("  (A venue reports a price, not a difference from a reference it")
    print("   never saw. 'Absent, not zero' has been the comment since v2.3;")
    print("   this is where it became a value.)")

    print("\n  Trades, which a broker does not report at all:")
    trades = expected_vs_live.entries_for(ComparisonMetric.TRADE_QUANTITY)
    print(f"    {trades[0].outcome.name}: {trades[0].detail}")

    # ----------------------------------------------------------------- #
    # 5. Money, per currency
    # ----------------------------------------------------------------- #

    print("\n[5] Realized P&L, keyed by currency")
    for entry in expected_vs_paper.entries_for(ComparisonMetric.REALIZED_PNL):
        print(
            f"    {entry.key}: expected={entry.expected} observed={entry.observed} "
            f"{entry.outcome.name}"
        )
    print("  (Never one number across two currencies: ADR-0020 removed that.)")

    # ----------------------------------------------------------------- #
    # 6. Tolerances are stated
    # ----------------------------------------------------------------- #

    print("\n[6] The same comparison with no tolerances stated")
    untoleranced = compare_runs(expected_view, paper_view, {})
    summarise("expected vs paper", untoleranced)
    print(f"    {untoleranced.entries[0].detail}")
    print("  (Two identical runs, and nothing is called a match: equality is")
    print("   a policy, and this one was not chosen by anybody.)")

    # ----------------------------------------------------------------- #
    # 7. Three at once
    # ----------------------------------------------------------------- #

    print("\n[7] All three pairs, so the divergence can be located")
    three_way = compare_expected_paper_live(expected_view, paper_view, live_view, TOLERANCES)
    for label, pair in (
        ("expected vs paper", three_way.expected_vs_paper),
        ("expected vs live", three_way.expected_vs_live),
        ("paper vs live", three_way.paper_vs_live),
    ):
        summarise(label, pair)
    print("  (Live differs from both and paper matches the backtest, so this is")
    print("   an execution difference and not a modelling one. Those two need")
    print("   different fixes, which is why all three pairs are produced.)")

    # ----------------------------------------------------------------- #

    print("\n[8] Invariants")
    slippage = expected_vs_live.entries_for(ComparisonMetric.SLIPPAGE)
    checks = (
        ("the paper run reproduces the backtest", expected_vs_paper.agrees),
        ("the live account does not", not expected_vs_live.agrees),
        (
            "a venue's slippage is missing, not zero",
            all(entry.observed is None for entry in slippage),
        ),
        (
            "and it is reported as missing",
            all(entry.outcome is ComparisonOutcome.MISSING_OBSERVED for entry in slippage),
        ),
        (
            "a metric with no tolerance is not comparable",
            all(
                entry.outcome is ComparisonOutcome.NOT_COMPARABLE
                for entry in untoleranced.entries
                if entry.expected is not None and entry.observed is not None
            ),
        ),
        (
            "P&L is keyed by currency",
            [e.key for e in expected_vs_paper.entries_for(ComparisonMetric.REALIZED_PNL)]
            == ["USD"],
        ),
        (
            "the comparison repeats exactly",
            compare_runs(expected_view, live_view, TOLERANCES) == expected_vs_live,
        ),
        (
            "neither side was changed",
            expected.state.portfolio.realized_pnl == paper.state.portfolio.realized_pnl,
        ),
    )
    for label, held in checks:
        print(f"  [{'ok' if held else 'FAILED'}] {label}")
    assert all(held for _, held in checks)

    print("\n" + "=" * 68)
    print("Example 44 complete.")
    print("(The venue records above are a deterministic fixture in this file.")
    print(" AlphaLab opened no connection and holds no vendor adapter.)")


if __name__ == "__main__":
    main()
