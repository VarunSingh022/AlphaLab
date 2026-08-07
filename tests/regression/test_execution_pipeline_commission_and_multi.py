"""Additional regression tests: commission propagation and sequential executions."""

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.execution.commission import FixedCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.market.events import QuoteReceived
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
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineConfig
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy, StrategyProtocol
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor


class QuoteIntentStrategy(BaseStrategy):
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


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


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
    # Return a runtime state with the strategy put in the running stage
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


def test_commission_propagation() -> None:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    strategy_state = _running_strategy_state(
        strategy_id, QuoteIntentStrategy(strategy_id, asset_id, Decimal("10"))
    )

    # Use a simulator with a fixed commission of 1.00 per fill
    sim = ExecutionSimulator(commission_model=FixedCommission(Decimal("1.00")))
    cfg = _config(strategy_id)
    cfg = replace(cfg, simulator=sim)

    state = ExecutionPipeline.initialize(cfg, strategy_state, 1.0)
    result = ExecutionPipeline.process_quote(state, _quote(asset_id), _context_factory)
    final_state = ExecutionPipeline.compile_analytics(result.state, 3.0)

    assert isinstance(result.market_event, QuoteReceived)
    # Execution report should include commission and portfolio cash
    # should reflect commission deduction
    report = result.execution_reports[0]
    assert report.commission == Decimal("1.00")
    assert final_state.portfolio.cash.balance("USD") == Decimal("98999.00")


def test_multiple_sequential_full_executions() -> None:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    strategy_state = _running_strategy_state(
        strategy_id, QuoteIntentStrategy(strategy_id, asset_id, Decimal("5"))
    )
    state = ExecutionPipeline.initialize(_config(strategy_id), strategy_state, 1.0)

    # First execution
    result1 = ExecutionPipeline.process_quote(state, _quote(asset_id), _context_factory)
    state2 = result1.state

    # Second execution using the updated state
    result2 = ExecutionPipeline.process_quote(state2, _quote(asset_id), _context_factory)
    final_state = ExecutionPipeline.compile_analytics(result2.state, 4.0)

    # Two sequential fills of 5 should yield position of 10
    position = final_state.portfolio.positions[asset_id]
    assert position.quantity == Decimal("10.000000")
    # Allocation notional reconciled after each full execution
    assert final_state.allocation.notional_allocated == Decimal("0.00")
