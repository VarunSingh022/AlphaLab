"""Runtime Engine orchestrating dispatch and state mutations."""

from collections.abc import Callable
from typing import Any

from alphalab.strategy.context import StrategyContext
from alphalab.strategy.dispatcher import Dispatcher
from alphalab.strategy.events import Intent, StrategyRuntimeEvent
from alphalab.strategy.state import RuntimeState


class StrategyEngine:
    """Pure functional aggregate root for the Strategy Runtime."""

    @staticmethod
    def process_event(
        state: RuntimeState,
        event: Any,
        context_factory: Callable[[str], StrategyContext],
        timestamp: float,
    ) -> tuple[RuntimeState, tuple[Intent, ...]]:
        """
        Dispatches a market or system event to all applicable running strategies.
        Returns the updated RuntimeState and aggregated Intents.
        """
        new_strategies = dict(state.strategies)
        aggregated_intents: list[Intent] = []
        new_events: list[StrategyRuntimeEvent] = []

        # At 100k events/sec, subscription index lookup happens here.
        # For this minimal core, we iterate and check status.
        for strategy_id, strategy_state in state.strategies.items():
            context = context_factory(strategy_id)
            updated_strat_state, intents, trans_evts = Dispatcher.dispatch_event(
                strategy_state, event, context, timestamp
            )

            new_strategies[strategy_id] = updated_strat_state
            aggregated_intents.extend(intents)
            new_events.extend(trans_evts)

        new_state = RuntimeState(
            strategies=new_strategies,
            events=(*state.events, *new_events),
        )
        return new_state, tuple(aggregated_intents)
