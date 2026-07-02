"""Pure state machine evaluating lifecycle transitions."""

import uuid
from dataclasses import replace
from typing import Any

from alphalab.strategy.events import LifecycleTransitioned
from alphalab.strategy.exceptions import InvalidTransitionError
from alphalab.strategy.state import LifecycleState, StrategyState


class RuntimeSupervisor:
    """Explicit, pure state machine functions for lifecycle transitions."""

    @staticmethod
    def _create_event_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _create_transition_event(
        strategy_id: str,
        old: LifecycleState,
        new: LifecycleState,
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
        if state.status not in {LifecycleState.CREATED, LifecycleState.FAILED}:
            raise InvalidTransitionError(f"Cannot configure from {state.status.name}")

        new_state = replace(state, status=LifecycleState.CONFIGURED, config=config, last_error=None)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, LifecycleState.CONFIGURED, timestamp
        )
        return new_state, event

    @staticmethod
    def initialize(
        state: StrategyState, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status != LifecycleState.CONFIGURED:
            raise InvalidTransitionError(f"Cannot initialize from {state.status.name}")

        new_state = replace(state, status=LifecycleState.INITIALIZED)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, LifecycleState.INITIALIZED, timestamp
        )
        return new_state, event

    @staticmethod
    def subscribe(
        state: StrategyState, subscriptions: frozenset[str], timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status != LifecycleState.INITIALIZED:
            raise InvalidTransitionError(f"Cannot subscribe from {state.status.name}")

        new_state = replace(state, status=LifecycleState.SUBSCRIBED, subscriptions=subscriptions)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, LifecycleState.SUBSCRIBED, timestamp
        )
        return new_state, event

    @staticmethod
    def start(
        state: StrategyState, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status not in {LifecycleState.SUBSCRIBED, LifecycleState.PAUSED}:
            raise InvalidTransitionError(f"Cannot start/resume from {state.status.name}")

        new_state = replace(state, status=LifecycleState.RUNNING)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, LifecycleState.RUNNING, timestamp
        )
        return new_state, event

    @staticmethod
    def pause(
        state: StrategyState, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status != LifecycleState.RUNNING:
            raise InvalidTransitionError(f"Cannot pause from {state.status.name}")

        new_state = replace(state, status=LifecycleState.PAUSED)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, LifecycleState.PAUSED, timestamp
        )
        return new_state, event

    @staticmethod
    def stop(state: StrategyState, timestamp: float) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status not in {LifecycleState.RUNNING, LifecycleState.PAUSED}:
            raise InvalidTransitionError(f"Cannot stop from {state.status.name}")

        new_state = replace(state, status=LifecycleState.STOPPING)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, LifecycleState.STOPPING, timestamp
        )
        return new_state, event

    @staticmethod
    def fail(
        state: StrategyState, error: str, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        new_state = replace(state, status=LifecycleState.FAILED, last_error=error)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, LifecycleState.FAILED, timestamp, reason=error
        )
        return new_state, event

    @staticmethod
    def complete_drain(
        state: StrategyState, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status != LifecycleState.STOPPING:
            raise InvalidTransitionError(f"Cannot complete drain from {state.status.name}")

        new_state = replace(state, status=LifecycleState.STOPPED)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, LifecycleState.STOPPED, timestamp
        )
        return new_state, event

    @staticmethod
    def dispose(
        state: StrategyState, timestamp: float
    ) -> tuple[StrategyState, LifecycleTransitioned]:
        if state.status not in {LifecycleState.STOPPED, LifecycleState.FAILED}:
            raise InvalidTransitionError(f"Cannot dispose from {state.status.name}")

        new_state = replace(state, status=LifecycleState.DISPOSED)
        event = RuntimeSupervisor._create_transition_event(
            state.strategy_id, state.status, LifecycleState.DISPOSED, timestamp
        )
        return new_state, event
