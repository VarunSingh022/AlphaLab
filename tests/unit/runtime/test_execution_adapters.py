from decimal import Decimal
from uuid import uuid4

from alphalab.core.enums import Side as CoreSide
from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.runtime.execution_adapters import canonical_execution_from_report


def test_canonical_execution_conversion() -> None:
    report = ExecutionReport(
        execution_id=str(uuid4()),
        order_id=str(uuid4()),
        asset_id=str(uuid4()),
        strategy_id=str(uuid4()),
        timestamp=1.0,
        fill_price=Decimal("100.00"),
        fill_quantity=Decimal("10"),
        commission=Decimal("1.00"),
        slippage=Decimal("0.00"),
        liquidity_flag="A",
        venue="SIM",
        currency="USD",
        status=FillStatus.FULL_FILL,
    )

    fill, trade = canonical_execution_from_report(report, CoreSide.BUY)

    assert fill.asset_id == report.asset_id
    assert fill.price == report.fill_price
    assert fill.quantity == report.fill_quantity
    assert trade.average_price == report.fill_price
    assert trade.quantity == report.fill_quantity
    assert trade.fill_ids and len(trade.fill_ids) == 1


def test_report_timestamp_flows_through_as_an_exact_float() -> None:
    """R4: report.timestamp (float) is carried straight onto Fill.filled_at /
    Trade.executed_at -- no datetime round-trip, no value change."""
    report = ExecutionReport(
        execution_id=str(uuid4()),
        order_id=str(uuid4()),
        asset_id=str(uuid4()),
        strategy_id=str(uuid4()),
        timestamp=1_712_345_678.123456,
        fill_price=Decimal("100.00"),
        fill_quantity=Decimal("10"),
        commission=Decimal("0.00"),
        slippage=Decimal("0.00"),
        liquidity_flag="A",
        venue="SIM",
        currency="USD",
        status=FillStatus.FULL_FILL,
    )

    fill, trade = canonical_execution_from_report(report, CoreSide.SELL)

    assert fill.filled_at == report.timestamp
    assert trade.executed_at == report.timestamp
    assert type(fill.filled_at) is float
    assert type(trade.executed_at) is float
    assert fill.filled_at is report.timestamp
    assert trade.executed_at is report.timestamp
