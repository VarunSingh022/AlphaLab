"""Deterministic binance Finance Client."""

from typing import Any

from alphalab.marketdata.feed import Bar, OrderBook, Quote, Trade
from alphalab.marketdata.timeframe import Timeframe


class binanceClient:
    """Isolated deterministic wrapper simulating binance APIs."""

    def connect(self) -> bool:
        return True

    def disconnect(self) -> bool:
        return True

    def subscribe(self, symbol: str, timeframe: Timeframe) -> bool:
        return True

    def unsubscribe(self, symbol: str, timeframe: Timeframe) -> bool:
        return True

    def request_history(
        self, symbol: str, timeframe: Timeframe, start: float, end: float
    ) -> tuple[Bar, ...]:
        return (Bar(symbol, start, 100.0, 105.0, 95.0, 102.0, 1000.0),)

    def latest_quote(self, symbol: str) -> Quote | None:
        return Quote(symbol, 1000.0, 101.0, 102.0, 10.0, 10.0)

    def latest_trade(self, symbol: str) -> Trade | None:
        return Trade(symbol, 1000.0, 101.5, 100.0)

    def latest_bar(self, symbol: str) -> Bar | None:
        return Bar(symbol, 1000.0, 100.0, 105.0, 95.0, 102.0, 1000.0)

    def order_book(self, symbol: str) -> OrderBook | None:
        return OrderBook(symbol, 1000.0, (), ())

    def health(self) -> dict[str, Any]:
        return {"status": "healthy"}
