"""Events that share the canonical market events' names and are not them.

Until v3.10 the repository shipped two such families of its own --
``alphalab.live.events`` (a ``TickReceived`` carrying ``provider_id`` /
``symbol`` / ``tick_type`` instead of a ``tick``) and ``alphalab.marketdata.events``
(``QuoteReceived`` / ``TradeReceived`` carrying a provider and a symbol). Those
packages were removed (ledger SCF-002), but the hazard they demonstrated is a
host application's to create at any time: its own event vocabulary will reuse
these names. These are the same shapes, defined outside
:mod:`alphalab.market.events`, so the tests that pin exact routing keep their
impostors.
"""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class TickReceived(BaseEvent):
    """The removed ``alphalab.live.events.TickReceived``: no ``tick`` field."""

    provider_id: str
    symbol: str
    tick_type: str


@dataclass(frozen=True, slots=True)
class QuoteReceived(BaseEvent):
    """The removed ``alphalab.marketdata.events.QuoteReceived``."""

    provider_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class TradeReceived(BaseEvent):
    """The removed ``alphalab.marketdata.events.TradeReceived``."""

    provider_id: str
    symbol: str
