"""What a strategy asked to receive, and the one test of it (ledger EXE-007).

Until v3.11 :meth:`~alphalab.strategy.supervisor.RuntimeSupervisor.subscribe`
recorded a set of strings and nothing read it: every running strategy received
every event for every asset, whatever it had declared, and a snapshot carried
the declaration faithfully while the run ignored it. A strategy trading one
instrument out of five hundred was built a context and called for all five
hundred.

The declaration is now the routing. A strategy receives an event exactly when
one of its subscriptions accepts it, and a strategy's context is not even built
for an event none of them accepts.

The grammar
-----------

A subscription is one string, so the set a strategy declares stays the
``frozenset[str]`` it has always been, and a snapshot records it as it did:

=====================  ====================================================
``"*"``                Everything this runtime delivers. The explicit
                       *all* form, and what a strategy that never declared
                       anything receives.
``"<topic>"``          Every event of one topic, for every asset.
``"<topic>:<asset>"``  One market topic for one asset -- ``"bars:<id>"``.
=====================  ====================================================

The topics are the hooks a strategy can be called on, named for what arrives:

===========  =================  ==============================================
Topic        Hook               What arrives
===========  =================  ==============================================
``ticks``    ``on_tick``        a canonical ``TickReceived``
``quotes``   ``on_quote``       a canonical ``QuoteReceived``
``trades``   ``on_trade``       a canonical ``TradeReceived``
``bars``     ``on_bar``         a canonical ``BarClosed``
``fills``    ``on_fill``        a fill of an order this strategy contributed to
``orders``   ``on_order``       what became of an order it contributed to
``timers``   ``on_timer``       a timer
``slices``   ``on_slice``       the close of an instant every record of which
                                has been published
``observ-    ``on_observation`` a point-in-time record, at the instant it
ations``                        became knowable (v3.12, ledger OFE-009)
===========  =================  ==============================================

The four market topics take an asset, and ``observations`` takes a subject --
``"observations:<subject>"`` is what one issuer's filings or one ticker's news
look like. A fill or an order event is already addressed to the strategies that
asked for the order, and a timer or a slice is not about one instrument.

Anything else is refused -- an unknown topic, an asset on a topic that has none,
a blank asset, an empty declaration. An empty set would be a strategy that can
never be called, which is a mistake rather than a configuration; a strategy that
wants everything says ``"*"``.

Routing by index (v3.12)
------------------------

Until v3.12 every event asked every strategy whether it wanted it, so each
strategy cost about a third of a microsecond on every record whatever it had
subscribed to: 10,000 strategies trading other instruments added 3.4 ms to
every record (the stress run's finding, ledger PRF-010). :class:`RoutingIndex`
answers the same question from the subscriptions themselves -- which strategies
take everything, which a whole topic, which one topic of one asset -- in the
order the strategies were registered, so the cost is the strategies an event
reaches.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from types import MappingProxyType
from typing import Final

from alphalab.strategy.events import (
    FillEvent,
    ObservationReceived,
    OrderEvent,
    SliceClosed,
    TimerEvent,
)
from alphalab.strategy.exceptions import StrategyValidationError

__all__ = [
    "MARKET_TOPICS",
    "SUBSCRIBE_ALL",
    "RoutingIndex",
    "Subscriptions",
    "Topic",
    "market_topic",
    "topic_of",
]

#: The explicit *all* form.
SUBSCRIBE_ALL: Final = "*"


@unique
class Topic(StrEnum):
    """What a strategy can subscribe to. See the module docstring."""

    TICKS = "ticks"
    QUOTES = "quotes"
    TRADES = "trades"
    BARS = "bars"
    FILLS = "fills"
    ORDERS = "orders"
    TIMERS = "timers"
    SLICES = "slices"
    OBSERVATIONS = "observations"


#: The topics a subscription may scope to one asset.
MARKET_TOPICS: Final = frozenset({Topic.TICKS, Topic.QUOTES, Topic.TRADES, Topic.BARS})

#: The topics a subscription may scope to one asset or subject.
_SCOPED_TOPICS: Final = MARKET_TOPICS | {Topic.OBSERVATIONS}

#: The module the canonical market vocabulary lives in. Named rather than
#: imported: ADR-0016 decision 3 keeps this package free of ``alphalab.market``.
_MARKET_EVENTS_MODULE: Final = "alphalab.market.events"

#: Canonical market event class -> (its topic, the attribute holding the payload
#: whose ``asset_id`` it is about).
_MARKET_EVENTS: Final[Mapping[str, tuple[Topic, str]]] = MappingProxyType(
    {
        "TickReceived": (Topic.TICKS, "tick"),
        "QuoteReceived": (Topic.QUOTES, "quote"),
        "TradeReceived": (Topic.TRADES, "tick"),
        "BarClosed": (Topic.BARS, "bar"),
    }
)


def market_topic(event: object) -> tuple[Topic, str] | None:
    """The topic and asset of a canonical market event, or ``None`` for anything else.

    Exact in the way :func:`~alphalab.strategy.dispatcher.market_hook_for` is: a
    class is canonical when its module *and* its name are, so a host
    application's own ``QuoteReceived`` has no topic and reaches no strategy.
    """

    event_type = type(event)
    if event_type.__module__ != _MARKET_EVENTS_MODULE:
        return None
    known = _MARKET_EVENTS.get(event_type.__name__)
    if known is None:
        return None
    topic, payload = known
    return topic, str(getattr(getattr(event, payload), "asset_id"))  # noqa: B009


def topic_of(event: object) -> tuple[Topic, str | None] | None:
    """The topic an event is delivered under, and its asset when it has one.

    ``None`` for an event no hook takes -- a depth update, a host application's
    look-alike of a canonical class -- which reaches no strategy, subscribed or
    not.
    """

    market = market_topic(event)
    if market is not None:
        return market
    if isinstance(event, FillEvent):
        return Topic.FILLS, None
    if isinstance(event, OrderEvent):
        return Topic.ORDERS, None
    if isinstance(event, TimerEvent):
        return Topic.TIMERS, None
    if isinstance(event, SliceClosed):
        return Topic.SLICES, None
    if isinstance(event, ObservationReceived):
        return Topic.OBSERVATIONS, event.subject
    return None


def _topic(text: str, declared: str) -> Topic:
    try:
        return Topic(text)
    except ValueError:
        known = ", ".join(sorted(topic.value for topic in Topic))
        raise StrategyValidationError(
            f"Subscription {declared!r} names no topic. Topics are {known}, or '*' for everything."
        ) from None


@dataclass(frozen=True, slots=True)
class Subscriptions:
    """A declared subscription set, parsed once and tested per event.

    Attributes:
        everything: The set holds ``"*"``.
        topics: Topics accepted for every asset.
        scoped: ``(topic, asset_id)`` pairs accepted for one asset each.
    """

    everything: bool
    topics: frozenset[Topic]
    scoped: frozenset[tuple[Topic, str]]

    @classmethod
    def parse(cls, declared: Iterable[str]) -> Subscriptions:
        """Parse and check a declaration.

        Raises:
            StrategyValidationError: If the declaration is empty, or any entry is
                not ``"*"``, a topic, or a market topic scoped to a non-blank
                asset.
        """

        entries = tuple(declared)
        if not entries:
            raise StrategyValidationError(
                "A strategy subscribed to nothing can never be called. Subscribe to the "
                "topics it handles, or to '*' for everything."
            )
        everything = False
        topics: set[Topic] = set()
        scoped: set[tuple[Topic, str]] = set()
        for entry in entries:
            if not isinstance(entry, str):
                raise StrategyValidationError(f"A subscription is a string, got {entry!r}.")
            if entry == SUBSCRIBE_ALL:
                everything = True
                continue
            name, separator, asset_id = entry.partition(":")
            topic = _topic(name, entry)
            if not separator:
                topics.add(topic)
                continue
            if topic not in _SCOPED_TOPICS:
                raise StrategyValidationError(
                    f"Subscription {entry!r} scopes {topic.value!r} to an asset; only market "
                    "topics (bars, quotes, ticks, trades) are about one asset, and "
                    "observations about one subject."
                )
            if not asset_id.strip():
                raise StrategyValidationError(f"Subscription {entry!r} names a blank asset.")
            scoped.add((topic, asset_id))
        return cls(everything, frozenset(topics), frozenset(scoped))

    def accepts(self, topic: Topic, asset_id: str | None = None) -> bool:
        """Whether an event of ``topic`` -- about ``asset_id``, if it has one -- is wanted."""

        if self.everything or topic in self.topics:
            return True
        return asset_id is not None and (topic, asset_id) in self.scoped


@dataclass(frozen=True, slots=True)
class RoutingIndex:
    """Which strategies each topic reaches, in registration order.

    Built from every strategy's :class:`Subscriptions`; :meth:`reaching` is
    exactly the strategies whose subscriptions accept an event, in the order
    they were registered, at the cost of the strategies it returns. Whether a
    strategy is running is not the index's: a caller reads that from the
    strategy's own state.

    Attributes:
        ids: Every strategy, in registration order; an index into it is a
            strategy's position.
        everything: The positions of the strategies subscribed to ``"*"``.
        topics: Topic -> the positions of the strategies subscribed to all of it.
        scoped: ``(topic, asset_id)`` -> the positions subscribed to that asset's
            topic alone.
    """

    ids: tuple[str, ...]
    everything: tuple[int, ...]
    topics: Mapping[Topic, tuple[int, ...]]
    scoped: Mapping[tuple[Topic, str], tuple[int, ...]]

    @classmethod
    def of(cls, routes: Iterable[tuple[str, Subscriptions]]) -> RoutingIndex:
        """The index of ``(strategy_id, subscriptions)`` pairs, in the order given."""

        ids: list[str] = []
        everything: list[int] = []
        topics: dict[Topic, list[int]] = {}
        scoped: dict[tuple[Topic, str], list[int]] = {}
        for position, (strategy_id, routing) in enumerate(routes):
            ids.append(strategy_id)
            if routing.everything:
                everything.append(position)
            for topic in routing.topics:
                topics.setdefault(topic, []).append(position)
            for key in routing.scoped:
                scoped.setdefault(key, []).append(position)
        return cls(
            tuple(ids),
            tuple(everything),
            MappingProxyType({topic: tuple(found) for topic, found in topics.items()}),
            MappingProxyType({key: tuple(found) for key, found in scoped.items()}),
        )

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """Pickled and copied as plain mappings, read-only again once rebuilt."""

        return (
            _routing_index,
            (self.ids, self.everything, dict(self.topics), dict(self.scoped)),
        )

    def reaching(self, topic: Topic, asset_id: str | None = None) -> tuple[str, ...]:
        """Every strategy whose subscriptions accept ``topic`` -- about ``asset_id`` -- in order.

        The strategies :meth:`Subscriptions.accepts` would accept, asked one by
        one in registration order, and no other.
        """

        lists = [
            found
            for found in (
                self.everything,
                self.topics.get(topic, ()),
                () if asset_id is None else self.scoped.get((topic, asset_id), ()),
            )
            if found
        ]
        if not lists:
            return ()
        positions = lists[0] if len(lists) == 1 else sorted(set().union(*lists))
        ids = self.ids
        return tuple(ids[position] for position in positions)


def _routing_index(
    ids: tuple[str, ...],
    everything: tuple[int, ...],
    topics: dict[Topic, tuple[int, ...]],
    scoped: dict[tuple[Topic, str], tuple[int, ...]],
) -> RoutingIndex:
    """A routing index rebuilt from its pickled parts."""

    return RoutingIndex(ids, everything, MappingProxyType(topics), MappingProxyType(scoped))
