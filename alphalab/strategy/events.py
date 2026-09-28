"""Events and Intent models crossing the Strategy boundary.

:data:`StrategyInboundEvent` is what :class:`~alphalab.strategy.dispatcher.Dispatcher`
accepts, and it is the narrowest type this package can honestly write.

Two families reach a strategy hook. The three declared here --
:class:`FillEvent`, :class:`OrderEvent`, :class:`TimerEvent` -- and the canonical
market vocabulary in ``alphalab.market.events``, which **cannot be named from
this package**: ADR-0016 decision 3 is normative that ``alphalab.strategy``
acquires no dependency on ``alphalab.market``, and
``tests/regression/test_instrument_identity_reaches_a_fill.py`` enforces it.

Both families derive from :class:`~alphalab.common.events.BaseEvent`, and
``alphalab.common`` is below both, so that is the supertype the union has in
common and the one this alias names. Until v2.17 the parameter was ``Any``,
which is what ADR-0032 category C finding 4 recorded; ``BaseEvent`` is a real
narrowing rather than a cosmetic one -- a bare ``object()``, a dict, a string or
an intent is now a static error at the call site instead of an event that
silently reaches no hook.

**It is deliberately not narrower, and the reason is the interesting part.**
What separates ``alphalab.market.events.TickReceived`` from another
``TickReceived`` (``alphalab.live.events`` had one until v3.10; a host
application may define its own) is the module it is defined in, and no
static type can express that -- which is exactly why
:func:`~alphalab.strategy.dispatcher.market_hook_for` discriminates on
``(module, name)`` at runtime. A ``Protocol`` here would admit the impostor, so
it would read as a stronger guarantee while being a weaker one. The precise
check stays where it can be precise, and the static type says what is true.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from alphalab.common.events import BaseEvent

__all__ = [
    "FillEvent",
    "Intent",
    "IntentKind",
    "LifecycleTransitioned",
    "OrderEvent",
    "StrategyInboundEvent",
    "StrategyRuntimeEvent",
    "TimerEvent",
]

#: What the dispatcher accepts. See the module docstring for why it is this and
#: not the exact union of routed classes.
type StrategyInboundEvent = BaseEvent


class IntentKind(StrEnum):
    """What an :class:`Intent`'s ``target`` states.

    One member in v3.10, and made explicit so the meaning is carried by the data
    rather than by a docstring. Until v3.10 :class:`Intent` described itself as a
    "target position/weight" while allocation treated ``target`` as an order
    delta, so a strategy that followed the docstring accumulated positions
    (ledger ALC-002). Position-aware target kinds are planned for v3.11; they
    will be new members, and a v3.10 intent will go on meaning what it means.
    """

    #: A signed order delta: how much to buy (positive) or sell (negative) now,
    #: in the units the configured sizing model reads -- a quantity for
    #: ``FixedQuantitySizing``, a notional for ``FixedDollarSizing``, a fraction
    #: of the strategy's capital for ``TargetWeightSizing``. It does not depend
    #: on the current position: emitting it twice asks for it twice.
    DELTA = "delta"


@dataclass(frozen=True, slots=True)
class Intent:
    """A signed order delta a strategy asks for.

    This is the sole mechanism for a strategy to affect the outside world.
    ``target`` is **not** a target position: it is how much to trade now, read
    by the run's sizing model -- see :class:`IntentKind`. Allocation nets the
    deltas of every strategy per instrument into one order. A strategy that
    wants to reach a position computes the difference itself -- from
    ``context.portfolio.quantity(asset)``, the account's filled position, and
    ``context.orders.net_quantity(asset)``, its share of working orders -- and
    asks for that.

    Attributes:
        strategy_id: The strategy asking.
        instrument: The asset the delta is for.
        target: The signed delta, in the sizing model's units.
        strength: A scale in ``[0, 1]`` the sizing model applies to ``target``.
        kind: What ``target`` states; :attr:`IntentKind.DELTA`, the only kind
            v3.10 defines.
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
    kind: IntentKind = IntentKind.DELTA


@dataclass(frozen=True, slots=True)
class StrategyRuntimeEvent(BaseEvent):
    """Base class for all Strategy Runtime system events."""

    pass


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
