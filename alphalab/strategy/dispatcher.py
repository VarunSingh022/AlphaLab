"""Pure event routing and hook execution."""

from collections.abc import Iterable
from typing import Any

from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import FillEvent, Intent, OrderEvent, TimerEvent
from alphalab.strategy.exceptions import InvalidIntentError
from alphalab.strategy.state import LifecycleState, StrategyState
from alphalab.strategy.supervisor import RuntimeSupervisor
from alphalab.strategy.validation import validate_intent


class Dispatcher:
    """Stateless router mapping events to strategy hooks."""

    @staticmethod
    def dispatch_event(
        strategy_state: StrategyState,
        event: Any,
        context: StrategyContext,
        timestamp: float,
    ) -> tuple[StrategyState, tuple[Intent, ...], tuple[Any, ...]]:
        """
        Invokes the appropriate hook on the strategy instance.
        Returns (NewStrategyState, EmittedIntents, LifecycleEvents).
        Exceptions transition the strategy to FAILED.
        """
        if strategy_state.status != LifecycleState.RUNNING:
            return strategy_state, (), ()

        instance = strategy_state.instance
        intents_iter: Iterable[Intent] = ()

        try:
            if isinstance(event, FillEvent):
                intents_iter = instance.on_fill(context, event)
            elif isinstance(event, OrderEvent):
                intents_iter = instance.on_order(context, event)
            elif isinstance(event, TimerEvent):
                intents_iter = instance.on_timer(context, event)
            # Assuming generic market events differentiate via class type or structure
            elif type(event).__name__ == "TickReceived":
                intents_iter = instance.on_tick(context, event)
            elif type(event).__name__ == "QuoteReceived":
                intents_iter = instance.on_quote(context, event)
            elif type(event).__name__ == "TradeReceived":
                intents_iter = instance.on_trade(context, event)
            elif type(event).__name__ == "BarClosed":
                intents_iter = instance.on_bar(context, event)

            valid_intents = []
            if intents_iter:
                for intent in intents_iter:
                    validate_intent(intent)
                    valid_intents.append(intent)

            return strategy_state, tuple(valid_intents), ()

        except InvalidIntentError as e:
            # A malformed intent drops the batch and fails the strategy
            # (Configurable thresholds can be added later; strict by default)
            failed_state, trans_evt = RuntimeSupervisor.fail(strategy_state, str(e), timestamp)
            return failed_state, (), (trans_evt,)

        except Exception as e:
            # Unhandled exceptions isolate and fail the strategy (G2)
            error_msg = f"HookExecutionError: {e!s}"
            failed_state, trans_evt = RuntimeSupervisor.fail(strategy_state, error_msg, timestamp)
            return failed_state, (), (trans_evt,)
