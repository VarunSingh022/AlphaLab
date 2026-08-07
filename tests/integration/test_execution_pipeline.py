"""Integration tests for the production execution pipeline."""

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.execution.fill import FillStatus
from alphalab.market.events import QuoteReceived
from alphalab.market.quote import Quote
from alphalab.oms.status import OrderStatus
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
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineConfig
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy, StrategyProtocol
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


class QuoteIntentStrategy(BaseStrategy):
    """Strategy that emits a deterministic quantity intent from quote events."""

    def __init__(self, strategy_id: str, asset_id: str, quantity: Decimal) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._quantity = quantity

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=self._quantity,
                timestamp=event.timestamp,
            ),
        )


def _context_factory(strategy_id: str) -> StrategyContext:
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


def _running_strategy_state(strategy_id: str, strategy: StrategyProtocol) -> StrategyRuntimeState:
    state = register_strategy(create_runtime(), strategy_id, strategy)
    strategy_state = state.strategies[strategy_id]
    configured, _ = RuntimeSupervisor.configure(strategy_state, {}, 1.0)
    initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
    subscribed, _ = RuntimeSupervisor.subscribe(initialized, frozenset({"quotes"}), 1.2)
    running, _ = RuntimeSupervisor.start(subscribed, 1.3)
    return replace(state, strategies={strategy_id: running})


def _risk_limits(max_quantity: Decimal = Decimal("1000")) -> RiskLimits:
    return RiskLimits(
        order_size=OrderSizeLimit(max_quantity, Decimal("1000000")),
        position=PositionLimit(Decimal("10000"), Decimal("1000000")),
        exposure=ExposureLimit(Decimal("2000000"), Decimal("2000000")),
        leverage=LeverageLimit(Decimal("10.0")),
        margin=MarginLimit(Decimal("1.00")),
        daily_loss=DailyLossLimit(Decimal("1000000")),
        drawdown=DrawdownLimit(Decimal("1.00")),
    )


def _config(
    strategy_id: str, starting_cash: Decimal = Decimal("100000")
) -> ExecutionPipelineConfig:
    return ExecutionPipelineConfig(
        account=Account("acct-1", "USD", "Integration Account", 1.0),
        starting_cash=starting_cash,
        budget=CapitalBudget(
            global_capital=starting_cash,
            maximum_exposure=starting_cash,
            cash_buffer=Decimal("0"),
            strategy_budgets={strategy_id: starting_cash},
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True,
            enforce_integer_quantities=False,
        ),
        risk_limits=_risk_limits(),
    )


def _quote(asset_id: str) -> Quote:
    return Quote(
        asset_id=asset_id,
        timestamp=2.0,
        bid=Decimal("99.00"),
        ask=Decimal("101.00"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        venue="SIM",
        currency="USD",
    )


def test_quote_drives_full_end_to_end_execution_pipeline() -> None:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    strategy_state = _running_strategy_state(
        strategy_id, QuoteIntentStrategy(strategy_id, asset_id, Decimal("10"))
    )
    state = ExecutionPipeline.initialize(_config(strategy_id), strategy_state, 1.0)

    result = ExecutionPipeline.process_quote(state, _quote(asset_id), _context_factory)
    final_state = ExecutionPipeline.compile_analytics(result.state, 3.0)

    assert isinstance(result.market_event, QuoteReceived)
    assert result.intents[0].instrument == asset_id
    assert result.order_requests[0].quantity == Decimal("10.000000")
    assert result.risk_decisions[0].approved is True

    # Allocation notional should be reconciled after full execution
    assert result.state.allocation.notional_allocated == Decimal("0.00")

    order = final_state.oms.orders.find(result.oms_orders[0].order_id)
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == Decimal("10.000000")
    assert order.average_fill_price == Decimal("100.00")

    report = result.execution_reports[0]
    assert report.status is FillStatus.FULL_FILL
    assert report.fill_price == Decimal("100.00")

    fill = result.fills[0]
    trade = result.trades[0]
    UUID(fill.fill_id)
    UUID(trade.trade_id)
    assert fill.order_id == str(order.order_id.value)
    assert trade.fill_ids == (fill.fill_id,)

    position = final_state.portfolio.positions[asset_id]
    assert position.quantity == Decimal("10.000000")
    assert position.average_cost == Decimal("100.0000")
    assert final_state.portfolio.cash.balance("USD") == Decimal("99000.00")
    assert len(final_state.analytics.reports) == 1
    assert final_state.analytics.reports[0].ending_capital == Decimal("100000.00")


def test_partial_execution_updates_oms_and_portfolio_without_closing_order() -> None:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    strategy_state = _running_strategy_state(
        strategy_id, QuoteIntentStrategy(strategy_id, asset_id, Decimal("10"))
    )
    state = ExecutionPipeline.initialize(_config(strategy_id), strategy_state, 1.0)

    result = ExecutionPipeline.process_quote(
        state,
        _quote(asset_id),
        _context_factory,
        FillStatus.PARTIAL_FILL,
        Decimal("4"),
    )

    order = result.state.oms.orders.find(result.oms_orders[0].order_id)
    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("4")
    assert order.remaining_quantity == Decimal("6.000000")
    assert result.execution_reports[0].status is FillStatus.PARTIAL_FILL
    assert result.fills[0].quantity == Decimal("4")
    assert result.state.portfolio.positions[asset_id].quantity == Decimal("4.000000")
    assert result.oms_orders[0].order_id in result.state.oms.active_orders

    # Partial execution should have reduced reserved notional by executed amount
    assert result.state.allocation.notional_allocated == Decimal("600.00")
