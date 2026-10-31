"""Immutable risk domain events."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.common.events import BaseEvent
from alphalab.core.order_request import OrderRequest


@dataclass(frozen=True, slots=True)
class RiskEvent(BaseEvent):
    pass


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


@dataclass(frozen=True, slots=True)
class MarginCall(RiskEvent):
    """The account's margin equity fell below its maintenance requirement (v4.2.0, FUT-017).

    Recorded at the market event a breach is found at, with what the run did
    about it: the liquidation orders it placed -- each strategy's own, through
    the canonical path -- or none, when no liquidation could be placed (one is
    already working, or nothing held can be sold).

    Attributes:
        equity: Margin equity: cash and positions at their collateral value, in
            the base currency.
        initial_requirement: What the positions require at their initial rate.
        maintenance_requirement: What they require at maintenance.
        deficit: What the liquidation restores: the initial requirement less the
            equity.
        orders: The liquidation orders placed, by order id; empty when none
            could be.
    """

    equity: Decimal
    initial_requirement: Decimal
    maintenance_requirement: Decimal
    deficit: Decimal
    orders: tuple[str, ...]
