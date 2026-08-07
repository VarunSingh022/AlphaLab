"""Public core domain API for AlphaLab."""

from alphalab.core.enums import AssetType, EventType, OrderStatus, OrderType, Side, TimeInForce
from alphalab.core.event import Event
from alphalab.core.events import (
    DomainEvent,
    EventDispatcher,
    EventHandler,
    EventMiddleware,
    EventNextHandler,
    EventPipeline,
    EventPriority,
    EventRegistry,
    LoggingMiddleware,
    Metadata,
    MetadataValue,
    PriorityEventQueue,
    ReplayEngine,
    TimingMiddleware,
)
from alphalab.core.exceptions import AlphaLabCoreError, DomainValidationError
from alphalab.core.fill import Fill
from alphalab.core.ids import (
    AccountId,
    AssetId,
    EventId,
    FillId,
    OrderId,
    PortfolioId,
    PositionId,
    SignalId,
    StrategyId,
    TradeId,
    new_account_id,
    new_asset_id,
    new_event_id,
    new_fill_id,
    new_order_id,
    new_portfolio_id,
    new_position_id,
    new_signal_id,
    new_strategy_id,
    new_trade_id,
    new_uuid,
    validate_uuid_id,
)
from alphalab.core.order import Order
from alphalab.core.portfolio import PortfolioState
from alphalab.core.position import Position
from alphalab.core.signal import Signal
from alphalab.core.trade import Trade

# Compatibility re-export for legacy OMS imports.
# The canonical business entity remains alphalab.core.order.Order.
OrderCompat = Order

__all__ = [
    "AccountId",
    "AlphaLabCoreError",
    "AssetId",
    "AssetType",
    "DomainEvent",
    "DomainValidationError",
    "Event",
    "EventDispatcher",
    "EventHandler",
    "EventId",
    "EventMiddleware",
    "EventNextHandler",
    "EventPipeline",
    "EventPriority",
    "EventRegistry",
    "EventType",
    "Fill",
    "FillId",
    "LoggingMiddleware",
    "Metadata",
    "MetadataValue",
    "Order",
    "OrderCompat",
    "OrderId",
    "OrderStatus",
    "OrderType",
    "PortfolioId",
    "PortfolioState",
    "Position",
    "PositionId",
    "PriorityEventQueue",
    "ReplayEngine",
    "Side",
    "Signal",
    "SignalId",
    "StrategyId",
    "TimeInForce",
    "TimingMiddleware",
    "Trade",
    "TradeId",
    "new_account_id",
    "new_asset_id",
    "new_event_id",
    "new_fill_id",
    "new_order_id",
    "new_portfolio_id",
    "new_position_id",
    "new_signal_id",
    "new_strategy_id",
    "new_trade_id",
    "new_uuid",
    "validate_uuid_id",
]
