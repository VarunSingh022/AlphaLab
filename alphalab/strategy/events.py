"""Events and Intent models crossing the Strategy boundary."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class Intent:
    """
    Statement of desired outcome (target position/weight) emitted by a strategy.
    This is the sole mechanism for a strategy to affect the outside world.
    """

    strategy_id: str
    instrument: str
    target: Decimal
    strength: Decimal = Decimal("1.0")
    horizon: str = "default"
    execution_directive: Mapping[str, Any] | None = None
    correlation_id: str = ""
    timestamp: float = 0.0
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategyRuntimeEvent:
    """Base class for all Strategy Runtime system events."""

    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class LifecycleTransitioned(StrategyRuntimeEvent):
    """Emitted when a strategy moves between lifecycle states."""

    strategy_id: str
    old_state: str
    new_state: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TimerEvent(StrategyRuntimeEvent):
    """Synthetic event injected by the Scheduler to wake up strategies."""

    timer_id: str
    schedule_type: str


@dataclass(frozen=True, slots=True)
class OrderEvent(StrategyRuntimeEvent):
    """Feedback event routing OMS order state changes back to the strategy."""

    order_id: str
    instrument: str
    status: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class FillEvent(StrategyRuntimeEvent):
    """Feedback event routing execution fills back to the strategy."""

    order_id: str
    instrument: str
    fill_quantity: Decimal
    fill_price: Decimal
