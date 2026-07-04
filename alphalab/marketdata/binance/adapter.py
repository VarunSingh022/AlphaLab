"""binance Provider Adapter."""

from typing import Any

from alphalab.marketdata.binance.client import binanceClient
from alphalab.marketdata.binance.config import binanceConfig
from alphalab.marketdata.feed import Bar, OrderBook, Quote, Trade
from alphalab.marketdata.timeframe import Timeframe


class binanceAdapter:
    __slots__ = ("_client", "_config")

    def __init__(self, config: binanceConfig) -> None:
        self._config = config
        self._client = binanceClient()

    def connect(self) -> bool: return self._client.connect()
    def disconnect(self) -> bool: return self._client.disconnect()
    def subscribe(self, symbol: str, timeframe: Timeframe) -> bool: 
        return self._client.subscribe(symbol, timeframe)
    def unsubscribe(self, symbol: str, timeframe: Timeframe) -> bool: 
        return self._client.unsubscribe(symbol, timeframe)
    
    def request_history(self, symbol: str, timeframe: Timeframe, start: float, end: float
    ) -> tuple[Bar, ...]:
        return self._client.request_history(symbol, timeframe, start, end)

    def latest_quote(self, symbol: str) -> Quote | None: return self._client.latest_quote(symbol)
    def latest_trade(self, symbol: str) -> Trade | None: return self._client.latest_trade(symbol)
    def latest_bar(self, symbol: str) -> Bar | None: return self._client.latest_bar(symbol)
    def order_book(self, symbol: str) -> OrderBook | None: return self._client.order_book(symbol)
    def health(self) -> dict[str, Any]: return self._client.health()