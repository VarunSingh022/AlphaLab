"""Canonical interface definitions for Strategy implementation."""

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import FillEvent, Intent, OrderEvent, TimerEvent


class StrategyProtocol(Protocol):
    """
    The strict Protocol every strategy must satisfy.
    All hooks are uniformly shaped and return Iterables of Intent.
    """

    def on_start(self, context: StrategyContext) -> None:
        """Declarative setup and warmup; runs before subscription."""
        ...

    def on_stop(self, context: StrategyContext) -> None:
        """Notification of graceful shutdown; no Intents may be emitted."""
        ...

    def on_shutdown(self, context: StrategyContext) -> Iterable[Intent]:
        """Final flattening/cancel intent emission during shutdown."""
        ...

    def on_tick(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        """React to a subscribed trade tick."""
        ...

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        """React to a subscribed Top-of-Book or L2 quote update."""
        ...

    def on_trade(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        """React to a subscribed market trade print."""
        ...

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        """React to a subscribed OHLCV bar close."""
        ...

    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        """React to an execution fill for this strategy's own order."""
        ...

    def on_order(self, context: StrategyContext, event: OrderEvent) -> Iterable[Intent]:
        """React to an OMS state change (ack, reject, cancel) for an order."""
        ...

    def on_timer(self, context: StrategyContext, event: TimerEvent) -> Iterable[Intent]:
        """React to a Scheduler timer."""
        ...


@runtime_checkable
class StrategyStateProtocol(Protocol):
    """The second protocol a strategy satisfies when it owns durable state.

    :class:`StrategyProtocol` declares ten hooks and no state accessor, so a
    strategy holding a rolling window, a counter or a fitted model holds state
    the runtime cannot see. This protocol is how a strategy says it has such
    state and how it describes it -- and it is deliberately *separate*, so that
    every existing strategy keeps satisfying ``StrategyProtocol`` unchanged and
    keeps its current, conditional continuation guarantee.

    Declaration is structural and opt-in: a strategy satisfies this by defining
    all three members, and nothing introspects an instance's attributes to guess
    at state. See ADR-0025 decisions 1 and 2.

    **Both directions are required.** A strategy defining some of these members
    and not others is refused at capture rather than treated as declaring
    nothing, because a payload written by an encode with no matching decode is a
    payload nothing can read back (ADR-0025 decision 3).

    The runtime asks at exactly two points, both of them between completed
    pipeline steps: :func:`alphalab.runtime.snapshot.capture` and
    :func:`alphalab.runtime.snapshot.restore`. Neither is a hook, neither runs
    per event, and neither adds a strategy dispatch.
    """

    def strategy_state_version(self) -> int:
        """The version of *this strategy's own* state shape.

        The strategy author's integer, not AlphaLab's. It is carried beside the
        payload and handed back to :meth:`restore_state`; nothing in AlphaLab
        interprets it, because nothing in AlphaLab knows what this strategy's
        version 2 would mean. Bumping it is how a strategy author changes their
        state shape without moving a framework constant (ADR-0025 decision 7).
        """
        ...

    def capture_state(self) -> Any:
        """Return this strategy's durable state, as data.

        Must return a value the existing
        :class:`~alphalab.persistence.serializer.DeterministicEncoder` accepts:
        a ``Decimal``, a dataclass, an ``Enum``, a ``UUID``, a JSON native, or a
        mapping or sequence of those. A type of the strategy's own that JSON
        cannot carry declares its projection through ``__serializable__``, the
        mechanism that already exists -- no encoder branch is added for
        strategies.

        Must be a **pure read**: it may not mutate the strategy, mint an
        identifier, emit an event, or touch a runtime service. A capture with
        side effects is a snapshot that depends on when it was taken, which is
        what makes a round trip untrustworthy (ADR-0025 decision 6).
        """
        ...

    def restore_state(self, payload: Any, version: int) -> None:
        """Rebuild this strategy's state from ``payload``, written at ``version``.

        ``payload`` is the JSON-decoded primitives, not the value
        :meth:`capture_state` returned: a ``Decimal`` arrives as a ``str`` and a
        tuple as a ``list``, because JSON records no type. Reconstructing them
        is what this method is for, and is why the contract is two-sided.

        May mutate **only this instance's own state**. It must not mint an
        identifier, emit an event, or process a record. Raising here refuses the
        whole restore, with the original exception chained (ADR-0025
        decisions 11 and 12).
        """
        ...


class BaseStrategy:
    """
    Ergonomic convenience ABC providing no-op defaults for StrategyProtocol.
    Authors may inherit from this or implement StrategyProtocol directly.

    Deliberately does **not** provide defaults for
    :class:`StrategyStateProtocol`: inheriting a no-op ``capture_state`` would
    make every strategy declare state it does not have.
    """

    def on_start(self, context: StrategyContext) -> None:
        pass

    def on_stop(self, context: StrategyContext) -> None:
        pass

    def on_shutdown(self, context: StrategyContext) -> Iterable[Intent]:
        return ()

    def on_tick(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return ()

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return ()

    def on_trade(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return ()

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return ()

    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        return ()

    def on_order(self, context: StrategyContext, event: OrderEvent) -> Iterable[Intent]:
        return ()

    def on_timer(self, context: StrategyContext, event: TimerEvent) -> Iterable[Intent]:
        return ()
