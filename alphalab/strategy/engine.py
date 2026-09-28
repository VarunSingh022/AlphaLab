"""Runtime Engine orchestrating dispatch and state mutations."""

from collections.abc import Callable, Iterable

from alphalab.strategy.context import StrategyContext
from alphalab.strategy.dispatcher import Dispatcher
from alphalab.strategy.events import Intent, StrategyInboundEvent, StrategyRuntimeEvent
from alphalab.strategy.state import LifecycleState, RuntimeState, StrategyState
from alphalab.strategy.subscription import topic_of


class StrategyEngine:
    """Pure functional aggregate root for the Strategy Runtime."""

    @staticmethod
    def process_event(
        state: RuntimeState,
        event: StrategyInboundEvent,
        context_factory: Callable[[str], StrategyContext],
        timestamp: float,
    ) -> tuple[RuntimeState, tuple[Intent, ...]]:
        """
        Dispatches a market or system event to all applicable running strategies.
        Returns the updated RuntimeState and aggregated Intents.

        **A strategy receives the event only when it subscribed to it** (ledger
        EXE-007): its declared subscriptions -- see
        :mod:`alphalab.strategy.subscription` -- must accept the event's topic,
        and its asset when the subscription names one. Until v3.11 the
        declaration was recorded and every running strategy received every
        event. An event with no topic reaches nobody, as before.

        ``context_factory`` is called once per *running, subscribed* strategy and
        not at all for the others. Until v2.10 it was called for every
        registered strategy and :class:`~alphalab.strategy.dispatcher.Dispatcher`
        discarded the result a moment later, which was free while a context held
        ``object()`` placeholders and becomes O(strategies x events) once one
        holds the marked portfolio; see ADR-0026 decision 7. A strategy that did
        not subscribe to the event now costs a set lookup.

        A strategy's first dispatch after it starts running is preceded by its
        ``on_start``, with the same context (ledger EXE-005).
        """
        topic = topic_of(event)
        if topic is None:
            return state, ()
        kind, asset_id = topic

        new_strategies: dict[str, StrategyState] | None = None
        aggregated_intents: list[Intent] = []
        new_events: list[StrategyRuntimeEvent] = []

        for strategy_id, strategy_state in state.strategies.items():
            if strategy_state.status is not LifecycleState.RUNNING:
                continue
            if not strategy_state.routing.accepts(kind, asset_id):
                continue

            context = context_factory(strategy_id)
            current, started_events = Dispatcher.start(strategy_state, context, timestamp)
            new_events.extend(started_events)
            intents: tuple[Intent, ...] = ()
            if current.status is LifecycleState.RUNNING:
                current, intents, trans_evts = Dispatcher.dispatch_event(
                    current, event, context, timestamp
                )
                new_events.extend(trans_evts)

            if current is not strategy_state:
                if new_strategies is None:
                    new_strategies = dict(state.strategies)
                new_strategies[strategy_id] = current
            aggregated_intents.extend(intents)

        if new_strategies is None and not new_events:
            return state, tuple(aggregated_intents)
        return (
            RuntimeState(
                strategies=new_strategies if new_strategies is not None else state.strategies,
                events=(*state.events, *new_events),
            ),
            tuple(aggregated_intents),
        )

    @staticmethod
    def deliver(
        state: RuntimeState,
        deliveries: Iterable[tuple[str, StrategyInboundEvent]],
        context_factory: Callable[[str], StrategyContext],
        timestamp: float,
    ) -> tuple[RuntimeState, tuple[Intent, ...]]:
        """Dispatch events addressed to one strategy each, in the order given.

        The execution pipeline's feedback path: a fill or an order event
        concerns the strategies that asked for the order, not every strategy
        running. Each ``(strategy_id, event)`` reaches that strategy when it is
        running and subscribed to the event's topic; one it did not subscribe to,
        or a strategy the runtime does not hold, is skipped. Returns the updated
        state and every intent the hooks returned, in delivery order.
        """

        strategies: dict[str, StrategyState] | None = None
        aggregated_intents: list[Intent] = []
        new_events: list[StrategyRuntimeEvent] = []

        for strategy_id, event in deliveries:
            held = strategies if strategies is not None else state.strategies
            strategy_state = held.get(strategy_id)
            if strategy_state is None or strategy_state.status is not LifecycleState.RUNNING:
                continue
            topic = topic_of(event)
            if topic is None or not strategy_state.routing.accepts(*topic):
                continue
            context = context_factory(strategy_id)
            current, started_events = Dispatcher.start(strategy_state, context, timestamp)
            new_events.extend(started_events)
            intents: tuple[Intent, ...] = ()
            if current.status is LifecycleState.RUNNING:
                current, intents, trans_evts = Dispatcher.dispatch_event(
                    current, event, context, timestamp
                )
                new_events.extend(trans_evts)
            if current is not strategy_state:
                if strategies is None:
                    strategies = dict(state.strategies)
                strategies[strategy_id] = current
            aggregated_intents.extend(intents)

        if strategies is None and not new_events:
            return state, tuple(aggregated_intents)
        return (
            RuntimeState(
                strategies=strategies if strategies is not None else state.strategies,
                events=(*state.events, *new_events),
            ),
            tuple(aggregated_intents),
        )

    @staticmethod
    def stop(
        state: RuntimeState,
        context_factory: Callable[[str], StrategyContext],
        timestamp: float,
        strategy_ids: Iterable[str] | None = None,
    ) -> tuple[RuntimeState, tuple[Intent, ...]]:
        """Stop running or paused strategies: ``on_shutdown``, then ``on_stop``, then ``STOPPED``.

        ``strategy_ids`` names which; ``None`` stops every one that is running
        or paused, in the runtime's order. Returns the updated state and the
        shutdown intents, which the caller routes -- see
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.stop_strategies`.
        """

        chosen = tuple(state.strategies) if strategy_ids is None else tuple(strategy_ids)
        strategies = dict(state.strategies)
        aggregated_intents: list[Intent] = []
        new_events: list[StrategyRuntimeEvent] = []
        for strategy_id in chosen:
            strategy_state = strategies.get(strategy_id)
            if strategy_state is None or strategy_state.status not in {
                LifecycleState.RUNNING,
                LifecycleState.PAUSED,
            }:
                continue
            context = context_factory(strategy_id)
            stopped, intents, events = Dispatcher.stop(strategy_state, context, timestamp)
            strategies[strategy_id] = stopped
            aggregated_intents.extend(intents)
            new_events.extend(events)
        if not new_events:
            return state, ()
        return (
            RuntimeState(strategies=strategies, events=(*state.events, *new_events)),
            tuple(aggregated_intents),
        )
