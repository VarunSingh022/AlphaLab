"""High-level instantiation and initial setup for the runtime."""

from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.state import LifecycleState, RuntimeState, StrategyState


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
        status=LifecycleState.CREATED,
        instance=instance,
    )
    return RuntimeState(strategies=new_strategies, events=state.events)
