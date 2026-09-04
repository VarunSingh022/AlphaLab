"""Regression guard for R1 (unified Side / OrderRequest on the execution path).

Before R1, ``alphalab.allocation`` and ``alphalab.risk`` each had their own
``OrderSide(Enum)`` and ``OrderRequest``, and ``execution_pipeline`` converted a
request field-by-field across the allocation -> risk -> OMS boundary. This test
drives a BUY and a SELL all the way through
allocation -> risk -> OMS -> execution -> portfolio and asserts:

* every ``side`` seen along the path is the *same* canonical
  ``alphalab.core.enums.Side`` object (identity, not a look-alike), and
* the resulting portfolio position is signed correctly for each direction.

If any package reintroduces a parallel side enum or request DTO, the identity
assertions here fail immediately.
"""

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.core.enums import Side
from alphalab.core.order_request import OrderRequest
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


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


class _TargetStrategy(BaseStrategy):
    def __init__(self, strategy_id: str, asset_id: str, target: Decimal) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._target = target

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=self._target,
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
        account=Account("acct-1", "USD", "Roundtrip Account", 1.0),
        starting_cash=cash,
        budget=CapitalBudget(
            global_capital=cash,
            maximum_exposure=cash,
            cash_buffer=Decimal("0"),
            strategy_budgets={strategy_id: cash},
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True,
            enforce_integer_quantities=False,
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


@pytest.mark.parametrize(
    ("target", "expected_side", "expected_position"),
    [
        (Decimal("10"), Side.BUY, Decimal("10.000000")),
        (Decimal("-10"), Side.SELL, Decimal("-10.000000")),
    ],
)
def test_side_is_canonical_and_signed_correctly_end_to_end(
    target: Decimal, expected_side: Side, expected_position: Decimal
) -> None:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    strategy_state = _running_strategy_state(
        strategy_id, _TargetStrategy(strategy_id, asset_id, target)
    )
    state = ExecutionPipeline.initialize(_config(strategy_id), strategy_state, 1.0)

    result = ExecutionPipeline.process_quote(state, _quote(asset_id), _context_factory)

    request = result.order_requests[0]
    oms_order = result.oms_orders[0]
    fill = result.fills[0]
    trade = result.trades[0]

    # The request DTO is the canonical core type, with a canonical Side member.
    assert isinstance(request, OrderRequest)
    assert request.side is expected_side
    assert type(request.side) is Side

    # The same Side object survives allocation -> OMS -> canonical fill/trade.
    assert oms_order.side is expected_side
    assert fill.side is expected_side
    assert trade.side is expected_side

    # Direction is applied correctly to the portfolio position.
    final_state = ExecutionPipeline.compile_analytics(result.state, 3.0)
    assert final_state.portfolio.positions[asset_id].quantity == expected_position
    assert result.risk_decisions[0].approved is True
