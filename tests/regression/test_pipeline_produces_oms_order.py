"""Regression guard for R3 (oms.order.Order is the canonical lifecycle Order).

Before R3, alphalab/core/order.py held a second, datetime-based, instruction-only
Order plus a dormant OMS<->core adapter (to_core_order / from_core_order /
canonical_order) that nothing called. R3 deleted core.order.Order and the
adapter, leaving alphalab.oms.order.Order as the single Order used across the
execution path.

This test drives the real execution pipeline and asserts the order it produces
is exactly alphalab.oms.order.Order -- with lifecycle state, no adapter hooks --
and that alphalab.core no longer exposes an Order at all.
"""

import importlib
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.market.quote import Quote
from alphalab.oms.order import Order as OMSOrder
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
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


class _BuyTenStrategy(BaseStrategy):
    def __init__(self, strategy_id: str, asset_id: str) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=Decimal("10"),
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


def _config(strategy_id: str) -> ExecutionPipelineConfig:
    cash = Decimal("100000")
    return ExecutionPipelineConfig(
        account=Account("acct-1", "USD", "R3 Account", 1.0),
        starting_cash=cash,
        budget=CapitalBudget(
            global_capital=cash,
            maximum_exposure=cash,
            cash_buffer=Decimal("0"),
            strategy_budgets={strategy_id: cash},
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True, enforce_integer_quantities=False
        ),
        risk_limits=RiskLimits(
            order_size=OrderSizeLimit(Decimal("1000"), Decimal("1000000")),
            position=PositionLimit(Decimal("10000"), Decimal("1000000")),
            exposure=ExposureLimit(Decimal("2000000"), Decimal("2000000")),
            leverage=LeverageLimit(Decimal("10.0")),
            margin=MarginLimit(Decimal("1.00")),
            daily_loss=DailyLossLimit(Decimal("1000000")),
            drawdown=DrawdownLimit(Decimal("1.00")),
        ),
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


def test_execution_pipeline_produces_oms_order() -> None:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    strategy_state = _running_strategy_state(strategy_id, _BuyTenStrategy(strategy_id, asset_id))
    state = ExecutionPipeline.initialize(_config(strategy_id), strategy_state, 1.0)

    result = ExecutionPipeline.process_quote(state, _quote(asset_id), _context_factory)

    order = result.oms_orders[0]
    assert type(order) is OMSOrder
    assert type(order).__module__ == "alphalab.oms.order"

    # It is the lifecycle order, not the deleted instruction-only one.
    assert hasattr(order, "status")
    assert hasattr(order, "filled_quantity")
    assert hasattr(order, "remaining_quantity")

    # The abandoned core.order adapter surface is gone.
    assert not hasattr(order, "to_core_order")
    assert not hasattr(order, "from_core_order")
    assert not hasattr(order, "canonical_order")


def test_core_no_longer_exposes_an_order() -> None:
    core = importlib.import_module("alphalab.core")
    assert not hasattr(core, "Order")
    assert "Order" not in core.__all__

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("alphalab.core.order")
