"""Small adapters converting execution reports to canonical core models.

Keep conversion logic separate from the runtime orchestrator so the
execution pipeline remains a thin coordinator and the canonical core
constructors live in a dedicated adapter boundary.
"""

from alphalab.core.enums import Side as CoreSide
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.ids import AssetId, OrderId, new_fill_id, new_trade_id
from alphalab.core.trade import Trade as CoreTrade
from alphalab.execution.report import ExecutionReport


def canonical_execution_from_report(
    report: ExecutionReport, side: CoreSide
) -> tuple[CoreFill, CoreTrade]:
    fill_id = new_fill_id()
    fill = CoreFill(
        fill_id=fill_id,
        order_id=OrderId(report.order_id),
        asset_id=AssetId(report.asset_id),
        side=side,
        quantity=report.fill_quantity,
        price=report.fill_price,
        filled_at=report.timestamp,
        commission=report.commission,
    )
    trade = CoreTrade(
        trade_id=new_trade_id(),
        asset_id=AssetId(report.asset_id),
        side=side,
        quantity=report.fill_quantity,
        average_price=report.fill_price,
        fill_ids=(fill_id,),
        executed_at=report.timestamp,
        order_id=OrderId(report.order_id),
    )
    return fill, trade
