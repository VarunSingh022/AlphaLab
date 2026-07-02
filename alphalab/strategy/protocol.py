"""Canonical interface definitions for Strategy implementation."""

from collections.abc import Iterable
from typing import Any, Protocol

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


class BaseStrategy:
    """
    Ergonomic convenience ABC providing no-op defaults for StrategyProtocol.
    Authors may inherit from this or implement StrategyProtocol directly.
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
