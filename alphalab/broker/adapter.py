"""Adapter translating OMS structures to Broker structures."""

from decimal import Decimal
from typing import Any, Protocol

from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.core.enums import OrderType as CoreOrderType
from alphalab.core.enums import Side as CoreSide
from alphalab.core.enums import TimeInForce


class OMSOrderProtocol(Protocol):
    """Generic interface for incoming OMS orders to decouple from strict file imports."""

    @property
    def order_id(self) -> str: ...

    @property
    def asset_id(self) -> str: ...

    @property
    def side(self) -> str: ...

    @property
    def quantity(self) -> Any: ...

    @property
    def price(self) -> Any: ...


class BrokerAdapter:
    """Stateless translator mapping generic OMS requests to Broker domain models."""

    @staticmethod
    def to_broker_order(
        oms_order: OMSOrderProtocol,
        broker_order_id: str,
        order_type: CoreOrderType,
        timestamp: float,
        *,
        stop_price: Decimal | None = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> BrokerOrder:
        """Converts an OMS order request into an immutable BrokerOrder.

        ``stop_price`` and ``time_in_force`` carry a stop or a lifetime other than
        the day to the venue (ledger EXE-003); a venue order with no stop records
        ``0``, as it always has.
        """

        side = CoreSide.BUY if str(oms_order.side).upper() == "BUY" else CoreSide.SELL

        return BrokerOrder(
            broker_order_id=broker_order_id,
            oms_order_id=oms_order.order_id,
            symbol=oms_order.asset_id,
            side=side,
            order_type=order_type,
            quantity=Decimal(str(oms_order.quantity)),
            price=Decimal(str(oms_order.price)),
            filled_quantity=Decimal("0.00"),
            average_fill_price=Decimal("0.00"),
            status=BrokerOrderStatus.PENDING_SUBMIT,
            created_at=timestamp,
            updated_at=timestamp,
            tif=time_in_force,
            stop_price=Decimal("0") if stop_price is None else stop_price,
        )
