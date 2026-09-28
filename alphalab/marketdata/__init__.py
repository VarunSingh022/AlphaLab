"""Market-data transports and the wire records a provider produces.

What remains of this package in v3.10 is what the canonical market model
(:mod:`alphalab.market`) consumes and nothing else:

* :mod:`~alphalab.marketdata.transport` -- a request/response HTTP transport
  over the standard library, and a static one for tests;
* :mod:`~alphalab.marketdata.websocket` -- an RFC 6455 client, the streaming
  half, used by :mod:`alphalab.market.stream`;
* :mod:`~alphalab.marketdata.feed` -- the wire records (a re-export of
  :mod:`alphalab.data.feed`), and :class:`~alphalab.marketdata.timeframe.Timeframe`,
  the resolution a :class:`~alphalab.market.provider.BarHistoryProvider` is
  asked for.

Until v3.10 it also shipped vendor-named clients (Binance, Databento, NSE,
Polygon, Yahoo -- four of them ``NotImplementedError`` stubs) and a v1 provider
engine with registries, connection managers, API-key configs and metrics that
nothing updated, alongside the equally unused ``alphalab.feed`` and
``alphalab.live`` packages. A vendor's endpoints, symbols and credentials are
the host application's; AlphaLab takes their output as wire records through
:mod:`alphalab.market.normalization`. They were removed (ledger BND-001,
SCF-002; ADR-0045).
"""

from alphalab.marketdata.exceptions import MarketDataError
from alphalab.marketdata.feed import Bar, OrderBook, OrderBookLevel, Quote, Trade
from alphalab.marketdata.timeframe import Timeframe
from alphalab.marketdata.transport import HttpTransport, StaticTransport, Transport
from alphalab.marketdata.websocket import (
    WebSocketConnection,
    WebSocketError,
    WebSocketTransport,
    connect_websocket,
)

__all__ = [
    "Bar",
    "HttpTransport",
    "MarketDataError",
    "OrderBook",
    "OrderBookLevel",
    "Quote",
    "StaticTransport",
    "Timeframe",
    "Trade",
    "Transport",
    "WebSocketConnection",
    "WebSocketError",
    "WebSocketTransport",
    "connect_websocket",
]
