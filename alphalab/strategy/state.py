"""Global and per-strategy immutable state tracking."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from alphalab.strategy.events import StrategyRuntimeEvent
from alphalab.strategy.protocol import StrategyProtocol


class LifecycleState(Enum):
    """Explicit pure state machine stages for a strategy instance."""

    CREATED = auto()
    CONFIGURED = auto()
    INITIALIZED = auto()
    SUBSCRIBED = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    STOPPED = auto()
    FAILED = auto()
    DISPOSED = auto()


@dataclass(frozen=True, slots=True)
class StrategyState:
    """
    Immutable state record for a single strategy.
    Contains the opaque instance pointer and lifecycle status.
    """

    strategy_id: str
    status: LifecycleState
    instance: StrategyProtocol
    config: Any = None
    subscriptions: frozenset[str] = field(default_factory=frozenset)
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """
    Global immutable state container for the Strategy Runtime.
    Tracks all managed strategies and global runtime events.
    """

    strategies: Mapping[str, StrategyState] = field(default_factory=dict)
    events: tuple[StrategyRuntimeEvent, ...] = field(default_factory=tuple)
