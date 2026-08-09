"""polygon market data client.

Not yet implemented. Every method previously returned identical hardcoded data
regardless of symbol, timeframe, or input -- silently fake, not a working
integration. Raising NotImplementedError is a deliberate improvement over that:
a caller relying on this provider now fails loudly at the point of use instead of
receiving fabricated data it might mistake for real. See
alphalab.marketdata.binance.client.binanceClient for the pattern a real
implementation should follow: depend on alphalab.marketdata.transport.Transport,
never hardcode a response.
"""

from typing import Any

from alphalab.marketdata.feed import Bar, OrderBook, Quote, Trade
from alphalab.marketdata.timeframe import Timeframe

_NOT_IMPLEMENTED = (
    "polygonClient is not yet implemented. It previously returned "
    "hardcoded fake data for every call; that has been removed rather than left "
    "silently misleading. See alphalab.marketdata.binance.client.binanceClient "
    "for the Transport-based pattern a real implementation should follow."
)


class polygonClient:
    """Not yet implemented. See module docstring."""

    def connect(self) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def disconnect(self) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def subscribe(self, symbol: str, timeframe: Timeframe) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def unsubscribe(self, symbol: str, timeframe: Timeframe) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def request_history(
        self, symbol: str, timeframe: Timeframe, start: float, end: float
    ) -> tuple[Bar, ...]:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def latest_quote(self, symbol: str) -> Quote | None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def latest_trade(self, symbol: str) -> Trade | None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def latest_bar(self, symbol: str) -> Bar | None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def order_book(self, symbol: str) -> OrderBook | None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def health(self) -> dict[str, Any]:
        return {"status": "not_implemented"}
