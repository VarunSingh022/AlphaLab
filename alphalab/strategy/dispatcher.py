"""Pure event routing and hook execution.

Market events are routed on their **fully qualified** type name --
``alphalab.market.events.TickReceived`` -- not on the bare class name.

Until v2.16 the four market branches read ``type(event).__name__ ==
"TickReceived"`` and three siblings, under a comment that began "Assuming
generic market events differentiate via class type or structure". A bare class
name is not a type: three packages in this repository define a class called
``TickReceived``, ``QuoteReceived`` or ``TradeReceived``, and the comparison
matched all of them.

``alphalab.live.events.TickReceived`` carries ``provider_id`` / ``symbol`` /
``tick_type`` where the canonical event carries a ``tick``. It was routed to
``on_tick``, the strategy read ``event.tick``, the ``AttributeError`` landed in
the handler below, and the strategy was transitioned to ``FAILED`` -- blamed for
a routing mistake it did not make. ``alphalab.marketdata.events`` collides the
same way on two more names.

**Why this is not ``isinstance``.** ADR-0016 decision 3 is normative:
"``alphalab.strategy`` acquires no dependency on ``alphalab.instrument`` or
``alphalab.market``". That layering is load-bearing -- it is what keeps
``Intent.instrument`` a bare, unvalidated ``str`` and keeps the strategy runtime
importable without the identity authority -- and
``tests/regression/test_instrument_identity_reaches_a_fill.py`` enforces it. The
canonical types are therefore not importable here. What *was* wrong was not the
absence of ``isinstance`` but the imprecision of the comparison: a module and a
name together identify a class exactly, and a bare name does not.
:mod:`alphalab.runtime.execution_pipeline`, which is above both layers and may
import both, uses ``isinstance`` on these same types; the two agree on every
canonical event, which ``tests/regression/test_strategy_event_routing.py``
asserts class by class rather than by assumption.

**What routes, and what deliberately does not.** ``TickReceived`` reaches
``on_tick``, ``QuoteReceived`` reaches ``on_quote``, ``TradeReceived`` reaches
``on_trade`` and ``BarClosed`` reaches ``on_bar``. ``BookUpdated`` and
``SnapshotCreated`` carry an :class:`~alphalab.market.snapshot.OrderBookSnapshot`
and reach no hook: :class:`~alphalab.strategy.protocol.StrategyProtocol` declares
no depth hook, and delivering a snapshot to ``on_quote`` -- whose every caller
today supplies a quote -- would hand existing strategies a payload with no
``quote`` attribute. The canonical record path cannot produce either event in any
case, because
:meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.publish_record`
refuses a record that is not a quote, bar or tick. That is a stated boundary,
pinned by the routing test, not an accident of the list.

**What ``event`` is typed as.** ``Any`` until v2.17, which ADR-0032 recorded as
category C finding 4: "narrowing it to the union it routes would let a caller's
mistake be a static error". ADR-0034 narrows it to
:data:`~alphalab.strategy.events.StrategyInboundEvent`, and does so *without*
moving hook selection into ``alphalab.runtime``, which ADR-0032 named as the
alternative. Moving it would have created a second dispatch authority beside
:data:`MARKET_EVENT_HOOKS`, which ADR-0032's ownership table assigns here; the
narrowing instead names the supertype both event families already share, which
is reachable from this package because ``alphalab.common`` is below it. See the
alias for why nothing narrower is honest.
"""

from collections.abc import Iterable

from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import (
    FillEvent,
    Intent,
    LifecycleTransitioned,
    OrderEvent,
    StrategyInboundEvent,
    TimerEvent,
)
from alphalab.strategy.exceptions import InvalidIntentError
from alphalab.strategy.state import LifecycleState, StrategyState
from alphalab.strategy.supervisor import RuntimeSupervisor
from alphalab.strategy.validation import validate_intent

#: The module the canonical market vocabulary lives in (ADR-0011). Named as a
#: string because ADR-0016 decision 3 forbids importing it from this package.
MARKET_EVENTS_MODULE = "alphalab.market.events"

#: Canonical market event -> the :class:`~alphalab.strategy.protocol.StrategyProtocol`
#: hook it reaches. An event of that name from any *other* module is not a
#: canonical market event and is not routed. Events absent from this table --
#: ``BookUpdated`` and ``SnapshotCreated`` -- reach no hook by decision; see the
#: module docstring.
MARKET_EVENT_HOOKS: dict[str, str] = {
    "TickReceived": "on_tick",
    "QuoteReceived": "on_quote",
    "TradeReceived": "on_trade",
    "BarClosed": "on_bar",
}


def market_hook_for(event: object) -> str | None:
    """The hook ``event`` routes to, or ``None`` when it is not routed here.

    Exact in both directions: a canonical market event resolves to its hook, and
    a class that merely shares its name -- ``alphalab.live.events.TickReceived``,
    ``alphalab.marketdata.events.QuoteReceived`` -- resolves to ``None``.
    """

    event_type = type(event)
    if event_type.__module__ != MARKET_EVENTS_MODULE:
        return None
    return MARKET_EVENT_HOOKS.get(event_type.__name__)


class Dispatcher:
    """Stateless router mapping events to strategy hooks."""

    @staticmethod
    def dispatch_event(
        strategy_state: StrategyState,
        event: StrategyInboundEvent,
        context: StrategyContext,
        timestamp: float,
    ) -> tuple[StrategyState, tuple[Intent, ...], tuple[LifecycleTransitioned, ...]]:
        """Invoke the hook ``event`` routes to, and return what it produced.

        Returns ``(new_strategy_state, emitted_intents, lifecycle_events)``.
        Exceptions transition the strategy to ``FAILED``.

        An event this dispatcher does not route leaves the strategy untouched
        and emits nothing, which is also what a non-running strategy does. See
        the module docstring for the routing table and what is outside it.

        ``event`` was ``Any`` until v2.17 (ADR-0032 category C finding 4). It is
        now :data:`~alphalab.strategy.events.StrategyInboundEvent`, which is the
        narrowest type this package can write without importing the canonical
        market vocabulary that ADR-0016 decision 3 forbids it -- see that alias
        for the whole reasoning. The runtime guard below is kept regardless: a
        value that is not an event reaches no hook and fails nothing, because a
        static type is a claim about callers that type-check and this is the one
        place a wrong claim would be charged to the *strategy*.
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
            else:
                hook = market_hook_for(event)
                if hook is not None:
                    intents_iter = getattr(instance, hook)(context, event)

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
