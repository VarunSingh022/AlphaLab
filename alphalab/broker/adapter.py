"""Adapter translating OMS structures to Broker structures."""

from typing import Any, Protocol

from alphalab.broker.order import (
    BrokerOrder,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerOrderType,
)


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
        order_type: BrokerOrderType,
        timestamp: float,
    ) -> BrokerOrder:
        """Converts an OMS order request into an immutable BrokerOrder."""
        from decimal import Decimal
        
        side = BrokerOrderSide.BUY if str(oms_order.side).upper() == "BUY" else BrokerOrderSide.SELL
        
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
        )