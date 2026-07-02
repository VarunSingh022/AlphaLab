"""Pure query functions exposing transparent Runtime access."""

from collections.abc import Sequence

from alphalab.strategy.state import LifecycleState, RuntimeState, StrategyState


def get_strategy(state: RuntimeState, strategy_id: str) -> StrategyState | None:
    """Returns the state of a specific strategy."""
    return state.strategies.get(strategy_id)


def active_strategies(state: RuntimeState) -> Sequence[StrategyState]:
    """Returns all strategies currently in the RUNNING state."""
    return tuple(s for s in state.strategies.values() if s.status == LifecycleState.RUNNING)


def failed_strategies(state: RuntimeState) -> Sequence[StrategyState]:
    """Returns all strategies currently in the FAILED state."""
    return tuple(s for s in state.strategies.values() if s.status == LifecycleState.FAILED)
