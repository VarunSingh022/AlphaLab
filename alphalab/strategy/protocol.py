"""Canonical interface definitions for Strategy implementation."""

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import (
    FillEvent,
    Intent,
    ObservationReceived,
    OrderEvent,
    SliceClosed,
    TimerEvent,
)


@runtime_checkable
class StrategyProtocol(Protocol):
    """
    The strict Protocol every strategy must satisfy.
    All hooks are uniformly shaped and return Iterables of Intent.

    ``runtime_checkable`` as of v2.17, which lets
    :meth:`~alphalab.strategy.registry.StrategyClassRegistry.construct` refuse a
    factory that returns something undispatchable *at registration* rather than
    at its first market event -- where
    :class:`~alphalab.strategy.dispatcher.Dispatcher` would record the failure
    against the strategy. As ever for a protocol, the check is on member
    presence and not on signatures; that is the right strength here, because
    what it is guarding against is a stub or a ``None``, not a subtly wrong hook.
    """

    def on_start(self, context: StrategyContext) -> None:
        """Setup and warmup, once, immediately before the first event it receives.

        Delivered by :class:`~alphalab.strategy.engine.StrategyEngine` when a
        running strategy is first dispatched an event it subscribed to, with that
        event's context -- so it sees what the run has seen, and nothing later.
        Raising fails the strategy before the event reaches it. Delivered once:
        a restored run records that it was (``StrategyState.started``). Until
        v3.11 nothing called it (ledger EXE-005).
        """
        ...

    def on_stop(self, context: StrategyContext) -> None:
        """Notification that the strategy is being stopped; no intents.

        Delivered by
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.stop_strategies`
        after :meth:`on_shutdown`, immediately before the strategy moves to
        ``STOPPED``.
        """
        ...

    def on_shutdown(self, context: StrategyContext) -> Iterable[Intent]:
        """Final intents -- flattening, typically -- as the strategy is stopped.

        Delivered by
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.stop_strategies`
        before :meth:`on_stop`. Its intents go through allocation, risk and the
        OMS like any others, as of the run's last instant.
        """
        ...

    def on_tick(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        """React to a subscribed trade tick."""
        ...

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        """React to a subscribed top-of-book quote.

        The event is an :class:`~alphalab.market.events.QuoteReceived`. Depth
        updates are *not* delivered here: they carry an
        :class:`~alphalab.market.snapshot.OrderBookSnapshot` rather than a
        quote, and no hook on this protocol takes one. See
        :mod:`alphalab.strategy.dispatcher` for the whole routing table.
        """
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
class SliceStrategyProtocol(Protocol):
    """The hook a cross-sectional strategy adds: an instant, complete (ledger EXE-004).

    Separate from :class:`StrategyProtocol`, as :class:`StrategyStateProtocol`
    is, so that every strategy written against the ten hooks keeps satisfying it
    unchanged. A strategy defining ``on_slice`` and subscribed to ``slices`` (or
    ``*``) is called after every record sharing an instant has been published;
    one without it is not called.
    """

    def on_slice(self, context: StrategyContext, event: SliceClosed) -> Iterable[Intent]:
        """React to a completed instant: every record at ``event.timestamp`` is in."""
        ...


class ObservationStrategyProtocol(Protocol):
    """The hook a strategy adds to act on external information (ledger OFE-009).

    Separate from :class:`StrategyProtocol`, as :class:`SliceStrategyProtocol`
    is, so every strategy written against the ten hooks keeps satisfying it. A
    strategy defining ``on_observation`` and subscribed to ``observations`` (or
    ``observations:<subject>``, or ``*``) is called when a point-in-time record
    becomes knowable; one without it is not called.
    """

    def on_observation(
        self, context: StrategyContext, event: ObservationReceived
    ) -> Iterable[Intent]:
        """React to a record that has just become knowable."""
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

    def on_slice(self, context: StrategyContext, event: SliceClosed) -> Iterable[Intent]:
        return ()

    def on_observation(
        self, context: StrategyContext, event: ObservationReceived
    ) -> Iterable[Intent]:
        return ()


def defines_on_observation(strategy: object) -> bool:
    """Whether ``strategy`` has an ``on_observation`` of its own (ledger OFE-009).

    :class:`BaseStrategy`'s answers nothing, so an observation reaches only a
    strategy that says what it does with one, and a run whose strategies define
    none delivers observations without building a context for any of them.
    """

    hook = getattr(type(strategy), "on_observation", None)
    return hook is not None and hook is not BaseStrategy.on_observation


def defines_on_slice(strategy: object) -> bool:
    """Whether ``strategy`` has an ``on_slice`` of its own (ledger EXE-004).

    :class:`BaseStrategy`'s answers nothing, so a strategy inheriting it
    unchanged has nothing to be called for: a run none of whose strategies
    defines the hook closes its slices without building a context or
    dispatching anything -- and without changing its state, so such a run is
    exactly what it was before slices existed.
    """

    hook = getattr(type(strategy), "on_slice", None)
    return hook is not None and hook is not BaseStrategy.on_slice
