"""High-level instantiation and initial setup for the runtime."""

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from alphalab.common.ids import use_id_source
from alphalab.strategy.exceptions import StrategyValidationError
from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.state import RuntimeState, StrategyState, StrategyStatus
from alphalab.strategy.supervisor import RuntimeSupervisor


def create_runtime() -> RuntimeState:
    """Initializes an empty Strategy Runtime state container."""
    return RuntimeState()


def register_strategy(
    state: RuntimeState, strategy_id: str, instance: StrategyProtocol
) -> RuntimeState:
    """Registers a fresh StrategyProtocol instance into the runtime."""
    new_strategies = dict(state.strategies)
    new_strategies[strategy_id] = StrategyState(
        strategy_id=strategy_id,
        status=StrategyStatus.CREATED,
        instance=instance,
    )
    return RuntimeState(strategies=new_strategies, events=state.events)


def start_strategy(
    state: RuntimeState,
    strategy_id: str,
    instance: StrategyProtocol,
    *,
    config: Any,
    subscriptions: Iterable[str],
    at: float,
) -> RuntimeState:
    """Register ``instance`` and take it to ``RUNNING``, keeping every other strategy (v4.0).

    The four :class:`~alphalab.strategy.supervisor.RuntimeSupervisor`
    transitions every run needs -- ``configure(config)``, ``initialize``,
    ``subscribe(subscriptions)``, ``start`` -- in order, at the instant ``at``,
    through the supervisor itself: this is a convenience over the lifecycle
    authority, not a second one. Each input is the caller's decision and none is
    defaulted -- a strategy subscribed to nothing is called for nothing.

    The supervisor's transition events are not added to ``state.events``, as
    the hand-written sequence this replaces did not add them, and they draw
    their identifiers from a stream of their own: a transition's event takes an
    identifier from whatever stream is current, so the same four calls made
    inside a run's seeded identifier scope would shift every identifier the run
    then mints, and a run set up inside its scope would differ from one set up
    outside it. Here they cannot.

    Raises:
        StrategyValidationError: If ``strategy_id`` is already registered.
            :func:`register_strategy` replaces an existing entry, which is how a
            caller swaps an instance deliberately; starting a second strategy
            under a running one's identity is not that, and is refused rather
            than allowed to replace it.
    """

    if strategy_id in state.strategies:
        raise StrategyValidationError(
            f"A strategy is already registered as {strategy_id!r}; start each strategy "
            "under its own identity."
        )
    if isinstance(subscriptions, str):
        raise StrategyValidationError(
            f"subscriptions is the string {subscriptions!r}, which would subscribe to each "
            f"of its characters; pass a collection of topic names, such as {{{subscriptions!r}}}."
        )
    registered = register_strategy(state, strategy_id, instance)
    entry = registered.strategies[strategy_id]
    with use_id_source(None):
        entry, _ = RuntimeSupervisor.configure(entry, config, at)
        entry, _ = RuntimeSupervisor.initialize(entry, at)
        entry, _ = RuntimeSupervisor.subscribe(entry, frozenset(subscriptions), at)
        entry, _ = RuntimeSupervisor.start(entry, at)
    return replace(registered, strategies={**registered.strategies, strategy_id: entry})
