"""Regression tests for execution pipeline failure boundaries."""

from decimal import Decimal
from uuid import uuid4

from alphalab.execution.events import ExecutionRejected
from alphalab.execution.fill import FillStatus
from alphalab.oms.status import OrderStatus
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineConfig
from tests.integration.test_execution_pipeline import (
    QuoteIntentStrategy,
    _config,
    _context_factory,
    _quote,
    _risk_limits,
    _running_strategy_state,
)


def test_rejected_execution_does_not_create_fill_trade_or_position() -> None:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    strategy_state = _running_strategy_state(
        strategy_id, QuoteIntentStrategy(strategy_id, asset_id, Decimal("10"))
    )
    state = ExecutionPipeline.initialize(_config(strategy_id), strategy_state, 1.0)

    result = ExecutionPipeline.process_quote(
        state, _quote(asset_id), _context_factory, FillStatus.REJECTED
    )

    order = result.state.oms.orders.find(result.oms_orders[0].order_id)
    assert order.status is OrderStatus.ACCEPTED
    assert result.execution_reports == ()
    assert result.fills == ()
    assert result.trades == ()
    assert asset_id not in result.state.portfolio.positions
    assert any(isinstance(event, ExecutionRejected) for event in result.state.execution.events)

    # Rejected execution should release allocation reservations
    assert result.state.allocation.notional_allocated == Decimal("0.00")


def test_risk_rejection_stops_before_oms_and_execution() -> None:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    strategy_state = _running_strategy_state(
        strategy_id, QuoteIntentStrategy(strategy_id, asset_id, Decimal("50"))
    )
    config = ExecutionPipelineConfig(
        account=_config(strategy_id).account,
        starting_cash=Decimal("100000"),
        budget=_config(strategy_id).budget,
        allocation_constraints=_config(strategy_id).allocation_constraints,
        risk_limits=_risk_limits(max_quantity=Decimal("10")),
    )
    state = ExecutionPipeline.initialize(config, strategy_state, 1.0)

    result = ExecutionPipeline.process_quote(state, _quote(asset_id), _context_factory)

    assert result.risk_decisions[0].approved is False
    assert result.oms_orders == ()
    assert result.execution_reports == ()
    assert result.fills == ()
    assert result.trades == ()
    assert len(result.state.oms.events) == 0
    assert len(result.state.execution.events) == 0
