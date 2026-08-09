"""Binance market data client.

Uses Binance's public (unauthenticated) REST API: `/api/v3/klines` for history,
`/api/v3/ticker/bookTicker` for quotes, `/api/v3/trades` for the latest trade, and
`/api/v3/depth` for order book snapshots. Endpoint shapes are written to Binance's
documented public API, but see `alphalab.marketdata.transport.HttpTransport`'s
docstring: this has not been verified against the live endpoint from within
AlphaLab's development environment, which has no network egress to external APIs.

Binance's `bookTicker` and `depth` endpoints do not return a timestamp field, unlike
`klines` and `trades`, which do. For those two, this client reads the system clock
at parse time -- an unavoidable non-determinism at the live-data ingestion boundary,
not a violation of AlphaLab's deterministic-computation principle, which applies to
what the rest of the system does with data once ingested, not to the boundary where
external, inherently time-varying data enters it.
"""

import json
import time
from typing import Any

from alphalab.marketdata.binance.config import binanceConfig
from alphalab.marketdata.exceptions import MarketDataValidationError
from alphalab.marketdata.feed import Bar, OrderBook, OrderBookLevel, Quote, Trade
from alphalab.marketdata.timeframe import Timeframe
from alphalab.marketdata.transport import Transport

_INTERVAL_BY_TIMEFRAME: dict[Timeframe, str] = {
    Timeframe.SECOND: "1s",
    Timeframe.MINUTE: "1m",
    Timeframe.HOURLY: "1h",
    Timeframe.DAILY: "1d",
}


def _interval_for(timeframe: Timeframe) -> str:
    interval = _INTERVAL_BY_TIMEFRAME.get(timeframe)
    if interval is None:
        raise MarketDataValidationError(
            f"Binance klines has no interval mapping for {timeframe.name}; "
            f"supported: {sorted(t.name for t in _INTERVAL_BY_TIMEFRAME)}."
        )
    return interval


class binanceClient:
    """Client for Binance's public market data REST endpoints."""

    __slots__ = ("_config", "_transport")

    def __init__(self, config: binanceConfig, transport: Transport) -> None:
        self._config = config
        self._transport = transport

    def connect(self) -> bool:
        """No-op: these are stateless REST GETs, not a persistent connection."""
        return True

    def disconnect(self) -> bool:
        """No-op: see `connect`."""
        return True

    def subscribe(self, symbol: str, timeframe: Timeframe) -> bool:
        """No-op: this client polls REST endpoints, it does not stream."""
        return True

    def unsubscribe(self, symbol: str, timeframe: Timeframe) -> bool:
        """No-op: see `subscribe`."""
        return True

    def request_history(
        self, symbol: str, timeframe: Timeframe, start: float, end: float
    ) -> tuple[Bar, ...]:
        """Fetches historical OHLCV bars via `/api/v3/klines`."""
        interval = _interval_for(timeframe)
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": str(int(start * 1000)),
            "endTime": str(int(end * 1000)),
        }
        body = self._transport.get(f"{self._config.base_url}/api/v3/klines", params)
        rows: list[list[Any]] = json.loads(body)

        return tuple(
            Bar(
                symbol=symbol,
                timestamp=float(row[0]) / 1000.0,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        )

    def latest_quote(self, symbol: str) -> Quote | None:
        """Fetches the current best bid/ask via `/api/v3/ticker/bookTicker`."""
        params = {"symbol": symbol}
        body = self._transport.get(f"{self._config.base_url}/api/v3/ticker/bookTicker", params)
        payload: dict[str, Any] = json.loads(body)
        if "bidPrice" not in payload:
            return None

        return Quote(
            symbol=symbol,
            timestamp=time.time(),
            bid=float(payload["bidPrice"]),
            ask=float(payload["askPrice"]),
            bid_size=float(payload["bidQty"]),
            ask_size=float(payload["askQty"]),
        )

    def latest_trade(self, symbol: str) -> Trade | None:
        """Fetches the most recent trade via `/api/v3/trades`."""
        params = {"symbol": symbol, "limit": "1"}
        body = self._transport.get(f"{self._config.base_url}/api/v3/trades", params)
        rows: list[dict[str, Any]] = json.loads(body)
        if not rows:
            return None

        latest = rows[-1]
        return Trade(
            symbol=symbol,
            timestamp=float(latest["time"]) / 1000.0,
            price=float(latest["price"]),
            size=float(latest["qty"]),
        )

    def latest_bar(self, symbol: str) -> Bar | None:
        """Fetches the most recent completed 1-minute bar."""
        now = time.time()
        bars = self.request_history(symbol, Timeframe.MINUTE, now - 120.0, now)
        return bars[-1] if bars else None

    def order_book(self, symbol: str) -> OrderBook | None:
        """Fetches a current order book snapshot via `/api/v3/depth`."""
        params = {"symbol": symbol, "limit": "20"}
        body = self._transport.get(f"{self._config.base_url}/api/v3/depth", params)
        payload: dict[str, Any] = json.loads(body)
        if "bids" not in payload:
            return None

        return OrderBook(
            symbol=symbol,
            timestamp=time.time(),
            bids=tuple(
                OrderBookLevel(price=float(level[0]), size=float(level[1]))
                for level in payload["bids"]
            ),
            asks=tuple(
                OrderBookLevel(price=float(level[0]), size=float(level[1]))
                for level in payload["asks"]
            ),
        )

    def health(self) -> dict[str, Any]:
        """Checks Binance API connectivity via `/api/v3/ping`."""
        try:
            self._transport.get(f"{self._config.base_url}/api/v3/ping", {})
            return {"status": "healthy"}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}
