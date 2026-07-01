"""Immutable risk domain events."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.risk.models import OrderRequest


@dataclass(frozen=True, slots=True)
class RiskEvent:
    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class RiskCheckStarted(RiskEvent):
    request: OrderRequest


@dataclass(frozen=True, slots=True)
class RiskApproved(RiskEvent):
    decision_id: str
    order_id: str


@dataclass(frozen=True, slots=True)
class RiskRejected(RiskEvent):
    decision_id: str
    order_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class MarginUpdated(RiskEvent):
    margin_used: Decimal
    available_margin: Decimal


@dataclass(frozen=True, slots=True)
class ExposureUpdated(RiskEvent):
    gross_exposure: Decimal
    net_exposure: Decimal


@dataclass(frozen=True, slots=True)
class DrawdownTriggered(RiskEvent):
    current_drawdown: Decimal
    max_drawdown: Decimal


@dataclass(frozen=True, slots=True)
class BuyingPowerUpdated(RiskEvent):
    buying_power: Decimal
