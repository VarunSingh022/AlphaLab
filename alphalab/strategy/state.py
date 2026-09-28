"""Global and per-strategy immutable state tracking."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from alphalab.strategy.events import StrategyRuntimeEvent
from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.subscription import SUBSCRIBE_ALL, Subscriptions


def _everything() -> frozenset[str]:
    return frozenset({SUBSCRIBE_ALL})


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
    """Immutable state record for a single strategy.

    Attributes:
        strategy_id: The strategy's identity in this runtime.
        status: Where it is in its lifecycle.
        instance: The strategy itself. Opaque to the runtime.
        config: What it was configured with.
        subscriptions: What it receives, in the grammar of
            :mod:`alphalab.strategy.subscription`. Routed on since v3.11; until
            then it was recorded and never read (ledger EXE-007). A strategy
            that never declared any receives everything (``"*"``), which is what
            every strategy received before.
        last_error: Why it failed, when it did.
        started: Whether ``on_start`` has been delivered. The runtime delivers
            it once, immediately before the strategy's first dispatch after it
            starts running (ledger EXE-005), and a restored run remembers that
            it did.

    Raises:
        StrategyValidationError: If ``subscriptions`` is not a valid declaration.
    """

    strategy_id: str
    status: LifecycleState
    instance: StrategyProtocol
    config: Any = None
    subscriptions: frozenset[str] = field(default_factory=_everything)
    last_error: str | None = None
    started: bool = False
    #: ``subscriptions``, parsed once. Derived, so it takes no part in equality.
    routing: Subscriptions = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "routing", Subscriptions.parse(self.subscriptions))


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """
    Global immutable state container for the Strategy Runtime.
    Tracks all managed strategies and global runtime events.
    """

    strategies: Mapping[str, StrategyState] = field(default_factory=dict)
    events: tuple[StrategyRuntimeEvent, ...] = field(default_factory=tuple)
