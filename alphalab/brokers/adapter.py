"""Adapters converting between AlphaLab Core formats and Broker Connector formats."""

from decimal import Decimal
from typing import Any

from alphalab.brokers.execution import ExecutionReport
from alphalab.brokers.order import BrokerOrder
from alphalab.core.enums import OrderStatus as CoreOrderStatus
from alphalab.core.enums import OrderType as CoreOrderType
from alphalab.core.enums import Side as CoreSide
from alphalab.core.enums import TimeInForce as CoreTimeInForce


class BrokerAdapter:
    """Stateless translator formatting dict payloads into pure Domain objects."""

    @staticmethod
    def dict_to_order(payload: dict[str, Any]) -> BrokerOrder:
        """Converts a standard OMS payload dictionary into a strictly typed BrokerOrder."""
        return BrokerOrder(
            order_id=str(payload["order_id"]),
            account_id=str(payload["account_id"]),
            symbol=str(payload["symbol"]),
            side=CoreSide[str(payload["side"]).upper()],
            order_type=CoreOrderType[str(payload["order_type"]).upper()],
            tif=CoreTimeInForce[str(payload.get("tif", "DAY")).upper()],
            quantity=Decimal(str(payload["quantity"])),
            price=Decimal(str(payload.get("price", "0.0"))),
            stop_price=Decimal(str(payload.get("stop_price", "0.0"))),
            filled_quantity=Decimal(str(payload.get("filled_quantity", "0.0"))),
            average_fill_price=Decimal(str(payload.get("average_fill_price", "0.0"))),
            status=CoreOrderStatus.PENDING,
            created_at=float(payload["timestamp"]),
            updated_at=float(payload["timestamp"]),
        )

    @staticmethod
    def execution_to_dict(execution: ExecutionReport) -> dict[str, Any]:
        """Converts an immutable ExecutionReport into a generalized event payload."""
        return {
            "execution_id": execution.execution_id,
            "order_id": execution.order_id,
            "account_id": execution.account_id,
            "symbol": execution.symbol,
            "fill_quantity": str(execution.fill_quantity),
            "fill_price": str(execution.fill_price),
            "commission": str(execution.commission),
            "timestamp": execution.timestamp,
        }
