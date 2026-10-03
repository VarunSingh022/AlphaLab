"""Global and per-strategy immutable state tracking."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from alphalab.strategy.events import StrategyRuntimeEvent
from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.subscription import SUBSCRIBE_ALL, RoutingIndex, Subscriptions


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


class _Reach:
    """Where a runtime state keeps its routing index once it is first built.

    The index is derived from the strategies, takes no part in the state's value,
    and is built at most once per state: a fresh state starts empty, and a change
    the runtime makes that keeps every strategy's id, order and subscriptions
    hands the index on (:meth:`RuntimeState.evolved`).
    """

    __slots__ = ("index",)

    def __init__(self) -> None:
        self.index: RoutingIndex | None = None

    def __reduce__(self) -> tuple[type[_Reach], tuple[()]]:
        """A copy or a pickle starts empty: the index is rebuilt from what it holds."""

        return (_Reach, ())


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """
    Global immutable state container for the Strategy Runtime.
    Tracks all managed strategies and global runtime events.
    """

    strategies: Mapping[str, StrategyState] = field(default_factory=dict)
    events: tuple[StrategyRuntimeEvent, ...] = field(default_factory=tuple)
    #: The routing index, built on first use. Derived: no part of equality.
    _reach: _Reach = field(default_factory=_Reach, init=False, repr=False, compare=False)

    @property
    def reach(self) -> RoutingIndex:
        """Which strategies each topic reaches, in registration order (v3.12).

        Built from the strategies' subscriptions the first time it is read, then
        kept with this state.
        """

        held = self._reach
        if held.index is None:
            held.index = RoutingIndex.of(
                (strategy_id, entry.routing) for strategy_id, entry in self.strategies.items()
            )
        return held.index

    def evolved(
        self,
        strategies: Mapping[str, StrategyState],
        events: tuple[StrategyRuntimeEvent, ...],
        changed: Iterable[str],
    ) -> RuntimeState:
        """This runtime with ``strategies`` and ``events``, keeping its routing index.

        For a change that replaces the ``changed`` strategies' states -- a
        start, a failure, a stop -- and leaves every strategy's id, order and
        subscriptions as they were. Anything else builds its index afresh.
        """

        state = RuntimeState(strategies=strategies, events=events)
        index = self._reach.index
        if (
            index is not None
            and len(strategies) == len(self.strategies)
            and all(
                strategy_id in self.strategies
                and strategies[strategy_id].routing == self.strategies[strategy_id].routing
                for strategy_id in changed
            )
        ):
            state._reach.index = index
        return state
