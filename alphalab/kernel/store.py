"""
State management container for immutable system state.

Handles event dispatch, history tracking, snapshots,
and deterministic state transitions.
"""

from typing import Any

from alphalab.kernel.reducer import ReducerRegistry
from alphalab.kernel.snapshot import Snapshot, SnapshotManager
from alphalab.kernel.state import SystemState


class StateStore:
    """Immutable state management container."""

    def __init__(
        self,
        initial_state: SystemState | None = None,
        registry: ReducerRegistry | None = None,
    ) -> None:
        self._state: SystemState = initial_state if initial_state is not None else SystemState()
        self._registry: ReducerRegistry = registry if registry is not None else ReducerRegistry()
        self._history: list[SystemState] = [self._state]
        self._snapshot_manager = SnapshotManager()

    def current_state(self) -> SystemState:
        """Returns the current active immutable state tree."""
        return self._state

    def dispatch(self, event: Any) -> SystemState:
        """Dispatches an event through the reducer registry, generating a new immutable state."""
        new_state = self._registry.reduce(self._state, event)
        if not isinstance(new_state, SystemState):
            raise TypeError(f"Reducer pipeline returned invalid type: {type(new_state)}")
        self._state = new_state
        self._history.append(new_state)
        return self._state

    def replace(self, state: SystemState) -> SystemState:
        """Overwrites the current state reference explicitly without processing reducers."""
        if not isinstance(state, SystemState):
            raise TypeError("Only SystemState instances can replace active store state.")
        self._state = state
        self._history.append(state)
        return self._state

    def history(self) -> tuple[SystemState, ...]:
        """Returns an immutable chronological sequence of all historic states."""
        return tuple(self._history)

    def snapshot(self, checkpoint_id: str) -> Snapshot:
        """Captures a snapshot of the current state via the underlying SnapshotManager."""
        return self._snapshot_manager.save(self._state, checkpoint_id)

    def restore(self, snapshot: Snapshot) -> SystemState:
        """Replaces current state with the state extracted from an immutable snapshot."""
        if not isinstance(snapshot, Snapshot):
            raise TypeError("Argument must be an instance of Snapshot.")
        return self.replace(snapshot.state)
