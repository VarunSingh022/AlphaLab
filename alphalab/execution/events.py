"""Immutable execution events."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class ExecutionEvent(BaseEvent):
    """Base class for all execution events."""

    order_id: str


@dataclass(frozen=True, slots=True)
class ExecutionSubmitted(ExecutionEvent):
    asset_id: str
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionCompleted(ExecutionEvent):
    execution_id: str
    fill_price: Decimal
    fill_quantity: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionPartiallyFilled(ExecutionEvent):
    execution_id: str
    fill_price: Decimal
    fill_quantity: Decimal
    remaining_quantity: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionRejected(ExecutionEvent):
    reason: str


@dataclass(frozen=True, slots=True)
class ExecutionExpired(ExecutionEvent):
    pass
