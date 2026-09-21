"""
AlphaLab Examples
=================

Example 45 : AlphaLab-to-Broker Reconciliation

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 05 (broker connection)
✓ Example 11 (unified backtest)
✓ Example 44 (expected vs paper vs live)

Topics
------

• Two reconciliations, two different pairs, two different causes
• Fourteen mismatch classes, each needing a different fix
• Which orders are expected at the venue, and which are not
• Venue symbols joined to AlphaLab identities through a supplied mapping
• A currency the broker account cannot speak about: unreconciled, not agreed
• Determinism and idempotency, so one report can be diffed against the next

What this shows
---------------

`broker.reconcile` has compared one pair since v2.3: `BrokerState` -- AlphaLab's
*mirror of the venue* -- against records the venue reported. It is unchanged and
still owns that pair.

This is the other pair: AlphaLab's *own execution state* -- the OMS book, the
portfolio and the fills it applied -- against that mirror. The two drift for
different reasons. The venue and the mirror drift because a message was lost;
the mirror and the book drift because a fill reached one and not the other. A
single function reporting both could not say which had happened.

Neither side is authoritative. A mismatch says what each side holds and stops:
re-sending an order that actually exists would duplicate it, so that decision
stays with the caller, along with every other one.

Run

    python examples/45_broker_reconciliation.py
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
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.broker.reconciliation import reconcile as reconcile_mirror
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import OrderStatus, OrderType
from alphalab.lifecycle import (
    MismatchCategory,
    ReconciliationTolerances,
    StateReconciliation,
    SymbolMapping,
    Tolerance,
    reconcile_execution_state,
)
from alphalab.market.quote import Quote
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine
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
from alphalab.runtime.broker_routing import broker_order_id_for
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig, ExecutionPipelineState
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

STRATEGY_ID = str(uuid4())
ASSET_ID = str(uuid4())
START_CASH = Decimal("1000000.00")
SEED = 450_000

MIDS = [Decimal("100.005"), Decimal("101.007"), Decimal("103.003"), Decimal("102.001")]

#: How close each compared number has to be. Required, with no default set:
#: a quantity tolerance right for a book of equities is wrong for one of
#: perpetuals quoted to eight decimal places.
TOLERANCES = ReconciliationTolerances(
    order_quantity=Tolerance(absolute=Decimal("0")),
    order_price=Tolerance(absolute=Decimal("0.01")),
    fill_quantity=Tolerance(absolute=Decimal("0")),
    fill_price=Tolerance(absolute=Decimal("0.01")),
    commission=Tolerance(absolute=Decimal("0.01")),
    position_quantity=Tolerance(absolute=Decimal("0.000001")),
    cash=Tolerance(absolute=Decimal("0.01")),
)


class ScriptedStrategy(BaseStrategy):
    def __init__(self) -> None:
        self._plan = {3.0: Decimal("400"), 4.0: Decimal("-150")}

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
    huge = Decimal("100000000")
    return RunConfig(
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-45", "USD", "Example 45", 1.0),
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
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(huge, huge),
                position=PositionLimit(huge, huge),
                exposure=ExposureLimit(huge, huge),
                leverage=LeverageLimit(Decimal("1000")),
                margin=MarginLimit(Decimal("1.00")),
                daily_loss=DailyLossLimit(huge),
                drawdown=DrawdownLimit(Decimal("1.00")),
            ),
        ),
        mode=ExecutionMode.BACKTEST,
        seed=SEED,
        start_timestamp=1.0,
    )


def dataset() -> MarketDataset:
    return MarketDataset.of(
        "EXAMPLE-45",
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


def agreeing_venue(result: BacktestResult) -> tuple[BrokerState, ExternalOrderMap]:
    """A venue whose records match the book exactly.

    A deterministic local fixture standing in for whatever an application's
    adapter produces. The handles are derived by
    ``broker_order_id_for`` -- the same function the live driver uses, so a
    retry after a lost response addresses the order the first attempt may have
    created rather than making a second one.
    """

    mapping = ExternalOrderMap()
    orders: dict[str, BrokerOrder] = {}
    executions: dict[str, BrokerExecution] = {}

    for order in result.orders:
        oms_order_id = str(order.order_id.value)
        handle = broker_order_id_for(order)
        mapping = mapping.bind(oms_order_id, handle)
        orders[handle] = BrokerOrder(
            broker_order_id=handle,
            oms_order_id=oms_order_id,
            symbol=order.asset_id,
            side=order.side,
            order_type=OrderType.MARKET,
            quantity=order.quantity,
            price=Decimal(order.metadata.get("reference_price", "0")),
            filled_quantity=order.filled_quantity,
            average_fill_price=order.average_fill_price,
            status=order.status,
            created_at=order.created_at,
            updated_at=order.updated_at,
        )

    handles = {str(order.order_id.value): broker_order_id_for(order) for order in result.orders}
    for report in result.state.execution.history.to_tuple():
        executions[report.execution_id] = BrokerExecution(
            execution_id=report.execution_id,
            broker_order_id=handles[report.order_id],
            symbol=report.asset_id,
            fill_quantity=report.fill_quantity,
            fill_price=report.fill_price,
            commission=report.commission,
            timestamp=report.timestamp,
        )

    positions = {
        asset_id: BrokerPosition(
            symbol=asset_id,
            quantity=position.quantity,
            average_price=position.average_cost,
            market_value=position.market_value,
            unrealized_pnl=position.unrealized_pnl,
            realized_pnl=position.realized_pnl,
            market_price=position.market_price,
        )
        for asset_id, position in result.state.portfolio.positions.items()
    }

    cash = result.state.portfolio.cash.balances.get("USD", Decimal("0.00"))
    return (
        BrokerState(
            broker_name="EXAMPLE-VENUE",
            connection_status=ConnectionStatus.CONNECTED,
            account=BrokerAccount("ACC-45", cash, cash, cash, Decimal("0"), cash, "USD"),
            positions=PersistentMap(positions),
            orders=PersistentMap(orders),
            executions=PersistentMap(executions),
        ),
        mapping,
    )


def show(result: StateReconciliation) -> None:
    print(
        f"  compared: {result.compared_orders} orders, {result.compared_fills} fills, "
        f"{result.compared_positions} instruments"
    )
    print(f"  reconciled={result.reconciled}  fully_reconciled={result.fully_reconciled}")
    for mismatch in result.mismatches:
        print(f"    {mismatch.category.name}")
        print(f"      key      : {mismatch.key}")
        print(f"      expected : {mismatch.expected}")
        print(f"      observed : {mismatch.observed}")
        print(f"      reason   : {mismatch.reason}")
    for area in result.unreconciled:
        print(f"    (not compared) {area.area}: {area.reason}")


def main() -> None:
    print("=" * 68)
    print("AlphaLab Example 45 : AlphaLab-to-Broker Reconciliation")
    print("=" * 68)

    result = BacktestEngine.run(run_config(), dataset(), running_strategy(), context_factory)
    pipeline: ExecutionPipelineState = result.state
    venue, mapping = agreeing_venue(result)
    symbols = SymbolMapping.identity()

    # ----------------------------------------------------------------- #
    # 1. Agreement
    # ----------------------------------------------------------------- #

    print("\n[1] A book and a venue that agree")
    print(
        f"  the run placed {len(result.orders)} orders and applied "
        f"{len(pipeline.execution.reports)} fills"
    )
    show(reconcile_execution_state(pipeline, venue, mapping, symbols, TOLERANCES))
    print("  (An empty report whose fully_reconciled is true is the only proof")
    print("   that the two sides agree about everything that was compared.)")

    # ----------------------------------------------------------------- #
    # 2. A fill the venue reports and the book has not applied
    # ----------------------------------------------------------------- #

    print("\n[2] A fill that never reached the book")
    lost = replace(
        pipeline,
        execution=replace(pipeline.execution, reports=PersistentMap()),
    )
    show(reconcile_execution_state(lost, venue, mapping, symbols, TOLERANCES))

    # ----------------------------------------------------------------- #
    # 3. An order the venue does not hold
    # ----------------------------------------------------------------- #

    print("\n[3] An order AlphaLab routed and the venue does not hold")
    first_handle = next(iter(venue.orders))
    without = replace(
        venue,
        orders=PersistentMap(
            {handle: order for handle, order in venue.orders.items() if handle != first_handle}
        ),
    )
    show(reconcile_execution_state(pipeline, without, mapping, symbols, TOLERANCES))

    # ----------------------------------------------------------------- #
    # 4. An order nobody routed
    # ----------------------------------------------------------------- #

    print("\n[4] An order at the venue that no binding names")
    stranger = BrokerOrder(
        broker_order_id="STRANGER-1",
        oms_order_id="not-ours",
        symbol=ASSET_ID,
        side=next(iter(result.orders)).side,
        order_type=OrderType.MARKET,
        quantity=Decimal("25"),
        price=Decimal("100.00"),
        filled_quantity=Decimal("0"),
        average_fill_price=Decimal("0"),
        status=OrderStatus.ACCEPTED,
        created_at=5.0,
        updated_at=5.0,
    )
    intruded = replace(venue, orders=venue.orders.set(stranger.broker_order_id, stranger))
    show(reconcile_execution_state(pipeline, intruded, mapping, symbols, TOLERANCES))

    # ----------------------------------------------------------------- #
    # 5. A position the venue sizes differently
    # ----------------------------------------------------------------- #

    print("\n[5] A position the two sides size differently")
    held = venue.positions[ASSET_ID]
    resized = replace(
        venue,
        positions=venue.positions.set(
            ASSET_ID, replace(held, quantity=held.quantity + Decimal("7"))
        ),
    )
    show(reconcile_execution_state(pipeline, resized, mapping, symbols, TOLERANCES))

    # ----------------------------------------------------------------- #
    # 6. A venue symbol that resolves to nothing
    # ----------------------------------------------------------------- #

    print("\n[6] Instrument identity")
    # The venue calls this instrument AAPL everywhere: on the order and on the
    # position. AlphaLab calls it by the identity derived from its declaration.
    renamed = replace(
        venue,
        positions=PersistentMap({"AAPL": replace(held, symbol="AAPL")}),
        orders=PersistentMap(
            {handle: replace(order, symbol="AAPL") for handle, order in venue.orders.items()}
        ),
    )
    unresolved = reconcile_execution_state(pipeline, renamed, mapping, SymbolMapping(), TOLERANCES)
    print(
        f"  with no mapping supplied     : "
        f"{len(unresolved.mismatches_in(MismatchCategory.INSTRUMENT_MISMATCH))} "
        "instrument mismatches"
    )
    print(f"    {unresolved.mismatches_in(MismatchCategory.INSTRUMENT_MISMATCH)[0].reason}")

    joined = reconcile_execution_state(
        pipeline, renamed, mapping, SymbolMapping(mapping={"AAPL": ASSET_ID}), TOLERANCES
    )
    print(
        f"  with the symbol joined to it : "
        f"{len(joined.mismatches_in(MismatchCategory.INSTRUMENT_MISMATCH))} "
        "instrument mismatches"
    )
    print("  (There is no default. ``SymbolMapping.identity()`` is a *named*")
    print("   choice a caller makes when a venue's symbols already are asset")
    print("   ids -- true of AlphaLab's own routing, and of nothing else.)")

    # ----------------------------------------------------------------- #
    # 7. A currency the account cannot speak about
    # ----------------------------------------------------------------- #

    print("\n[7] A book in two currencies against a single-currency account")
    mixed = replace(
        pipeline,
        portfolio=PortfolioEngine.apply_deposit(
            pipeline.portfolio, Decimal("250000.00"), "JPY", 9.0
        ),
    )
    show(reconcile_execution_state(mixed, venue, mapping, symbols, TOLERANCES))

    # ----------------------------------------------------------------- #
    # 8. The other reconciliation
    # ----------------------------------------------------------------- #

    print("\n[8] The pair broker.reconcile owns")
    mirror = reconcile_mirror(
        venue, list(venue.orders.values()), list(venue.positions.values()), venue.account
    )
    print(f"  mirror vs the venue's records : reconciled={mirror.reconciled}")
    print(
        f"  the book vs the mirror        : reconciled="
        f"{reconcile_execution_state(pipeline, venue, mapping, symbols, TOLERANCES).reconciled}"
    )
    print("  (Two questions. An empty result from one says nothing about the")
    print("   other, which is why both exist.)")

    # ----------------------------------------------------------------- #

    print("\n[9] Invariants")
    clean = reconcile_execution_state(pipeline, venue, mapping, symbols, TOLERANCES)
    again = reconcile_execution_state(pipeline, venue, mapping, symbols, TOLERANCES)
    broken = reconcile_execution_state(lost, venue, mapping, symbols, TOLERANCES)
    checks = (
        ("an agreeing pair reconciles fully", clean.fully_reconciled),
        ("repeating it returns an equal report", clean == again),
        (
            "a lost fill is found",
            len(broken.mismatches_in(MismatchCategory.UNEXPECTED_OBSERVED_FILL)) == 2,
        ),
        ("neither side was mutated", pipeline == result.state and venue.orders == venue.orders),
        (
            "reconciled and fully_reconciled are different questions",
            reconcile_execution_state(mixed, venue, mapping, symbols, TOLERANCES).reconciled
            and not reconcile_execution_state(
                mixed, venue, mapping, symbols, TOLERANCES
            ).fully_reconciled,
        ),
        (
            "every mismatch names both sides or says one is absent",
            all(m.expected is not None or m.observed is not None for m in broken.mismatches),
        ),
        (
            "mismatches come out in category order",
            [m.category.name for m in broken.mismatches]
            == sorted(
                (m.category.name for m in broken.mismatches),
                key=lambda name: list(MismatchCategory).index(MismatchCategory[name]),
            ),
        ),
    )
    for label, held in checks:
        print(f"  [{'ok' if held else 'FAILED'}] {label}")
    assert all(held for _, held in checks)

    print("\n" + "=" * 68)
    print("Example 45 complete.")
    print("(The venue state above is a fixture in this file. Nothing here opened")
    print(" a connection, held a credential or named a vendor -- and nothing")
    print(" here decided which side was right.)")


if __name__ == "__main__":
    main()
