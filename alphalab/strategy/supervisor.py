"""Pure state machine evaluating lifecycle transitions."""

from dataclasses import replace
from typing import Any

from alphalab.common.ids import new_id
from alphalab.strategy.events import LifecycleTransitioned
from alphalab.strategy.exceptions import InvalidTransitionError
from alphalab.strategy.state import StrategyState, StrategyStatus


class RuntimeSupervisor:
    """Explicit, pure state machine functions for lifecycle transitions."""

    @staticmethod
    def _create_event_id() -> str:
        return str(new_id())

    @staticmethod
    def _create_transition_event(
        strategy_id: str,
        old: StrategyStatus,
        new: StrategyStatus,
        timestamp: float,
        reason: str = "",
    ) -> LifecycleTransitioned:
        return LifecycleTransitioned(
            event_id=RuntimeSupervisor._create_event_id(),
            timestamp=timestamp,
            strategy_id=strategy_id,
            old_state=old.name,
            new_state=new.name,
            reason=reason,
        )

    @staticmethod
    def configure(
        state: StrategyState, config: Any, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status not in {StrategyStatus.CREATED, StrategyStatus.FAILED}:
            raise InvalidTransitionError(f"Cannot configure from {state.status.name}")

        new_state = replace(state, status=StrategyStatus.CONFIGURED, config=config, last_error=None)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, StrategyStatus.CONFIGURED, timestamp
        )
        return new_state, event

    @staticmethod
    def initialize(
        state: StrategyState, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status != StrategyStatus.CONFIGURED:
            raise InvalidTransitionError(f"Cannot initialize from {state.status.name}")

        new_state = replace(state, status=StrategyStatus.INITIALIZED)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, StrategyStatus.INITIALIZED, timestamp
        )
        return new_state, event

    @staticmethod
    def subscribe(
        state: StrategyState, subscriptions: frozenset[str], timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status != StrategyStatus.INITIALIZED:
            raise InvalidTransitionError(f"Cannot subscribe from {state.status.name}")

        new_state = replace(state, status=StrategyStatus.SUBSCRIBED, subscriptions=subscriptions)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, StrategyStatus.SUBSCRIBED, timestamp
        )
        return new_state, event

    @staticmethod
    def start(
        state: StrategyState, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status not in {StrategyStatus.SUBSCRIBED, StrategyStatus.PAUSED}:
            raise InvalidTransitionError(f"Cannot start/resume from {state.status.name}")

        new_state = replace(state, status=StrategyStatus.RUNNING)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, StrategyStatus.RUNNING, timestamp
        )
        return new_state, event

    @staticmethod
    def pause(
        state: StrategyState, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status != StrategyStatus.RUNNING:
            raise InvalidTransitionError(f"Cannot pause from {state.status.name}")

        new_state = replace(state, status=StrategyStatus.PAUSED)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, StrategyStatus.PAUSED, timestamp
        )
        return new_state, event

    @staticmethod
    def stop(state: StrategyState, timestamp: float) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status not in {StrategyStatus.RUNNING, StrategyStatus.PAUSED}:
            raise InvalidTransitionError(f"Cannot stop from {state.status.name}")

        new_state = replace(state, status=StrategyStatus.STOPPING)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, StrategyStatus.STOPPING, timestamp
        )
        return new_state, event

    @staticmethod
    def fail(
        state: StrategyState, error: str, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        new_state = replace(state, status=StrategyStatus.FAILED, last_error=error)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, StrategyStatus.FAILED, timestamp, reason=error
        )
        return new_state, event

    @staticmethod
    def complete_drain(
        state: StrategyState, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status != StrategyStatus.STOPPING:
            raise InvalidTransitionError(f"Cannot complete drain from {state.status.name}")

        new_state = replace(state, status=StrategyStatus.STOPPED)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, StrategyStatus.STOPPED, timestamp
        )
        return new_state, event

    @staticmethod
    def dispose(
        state: StrategyState, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status not in {StrategyStatus.STOPPED, StrategyStatus.FAILED}:
            raise InvalidTransitionError(f"Cannot dispose from {state.status.name}")

        new_state = replace(state, status=StrategyStatus.DISPOSED)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, StrategyStatus.DISPOSED, timestamp
        )
        return new_state, event
