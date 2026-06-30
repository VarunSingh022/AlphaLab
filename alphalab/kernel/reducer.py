"""Composable reduction engine transforming (State, Event) -> State."""

from typing import Any, Protocol


class Reducer(Protocol):
    """Protocol defining functional stateless state reducers."""

    def __call__(self, state: Any, event: Any) -> Any: ...


class ReducerRegistry:
    """Centralized, composable reducer dispatcher."""

    def __init__(self) -> None:
        self._reducers: list[Reducer] = []

    def register(self, reducer: Reducer) -> None:
        """Registers a pure reducer function into the sequential pipeline."""
        if reducer not in self._reducers:
            self._reducers.append(reducer)

    def unregister(self, reducer: Reducer) -> None:
        """Removes a previously registered reducer."""
        if reducer in self._reducers:
            self._reducers.remove(reducer)

    def reduce(self, state: Any, event: Any) -> Any:
        """Sequentially folds the state tree through all registered reducers."""
        current_state = state
        for reducer in self._reducers:
            current_state = reducer(current_state, event)
        return current_state
