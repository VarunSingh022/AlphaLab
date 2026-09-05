"""Top-level Engine Facade orchestrating unified Market Data."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.marketdata.cache import CacheRecord
from alphalab.marketdata.config import ProviderConfig
from alphalab.marketdata.events import BarReceived, OrderBookUpdated, QuoteReceived, TradeReceived
from alphalab.marketdata.feed import Bar, OrderBook, Quote, Trade
from alphalab.marketdata.manager import ConnectionManager
from alphalab.marketdata.protocol import MarketDataProtocol
from alphalab.marketdata.registry import ProviderRegistry
from alphalab.marketdata.state import MarketDataState
from alphalab.marketdata.timeframe import Timeframe


class MarketDataEngine:
    """Facade for managing deterministic multi-vendor market data."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def initialize(engine_id: str) -> MarketDataState:
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        return MarketDataState(engine_id=engine_id)

    @staticmethod
    def register(state: MarketDataState, config: ProviderConfig, ts: float) -> MarketDataState:
        return ProviderRegistry.register(state, config, ts)

    @staticmethod
    def connect(
        state: MarketDataState, provider_id: str, provider: MarketDataProtocol, ts: float
    ) -> MarketDataState:
        return ConnectionManager.connect(state, provider_id, provider, ts)

    @staticmethod
    def disconnect(
        state: MarketDataState, provider_id: str, provider: MarketDataProtocol, ts: float
    ) -> MarketDataState:
        return ConnectionManager.disconnect(state, provider_id, provider, ts)

    @staticmethod
    def subscribe(
        state: MarketDataState,
        provider_id: str,
        provider: MarketDataProtocol,
        symbol: str,
        tf: Timeframe,
        ts: float,
    ) -> MarketDataState:
        return ConnectionManager.subscribe(state, provider_id, provider, symbol, tf, ts)

    @staticmethod
    def unsubscribe(
        state: MarketDataState,
        provider_id: str,
        provider: MarketDataProtocol,
        symbol: str,
        tf: Timeframe,
        ts: float,
    ) -> MarketDataState:
        return ConnectionManager.unsubscribe(state, provider_id, provider, symbol, tf, ts)

    @staticmethod
    def request_history(
        state: MarketDataState,
        provider_id: str,
        provider: MarketDataProtocol,
        symbol: str,
        tf: Timeframe,
        start: float,
        end: float,
    ) -> MarketDataState:
        history = provider.request_history(symbol, tf, start, end)
        cache_key = f"{provider_id}:{symbol}:{tf.name}"

        new_cache = replace(
            state.cache,
            records=state.cache.records.set(cache_key, CacheRecord(symbol, provider_id, history)),
        )

        return replace(state, cache=new_cache)

    @staticmethod
    def process_quote(
        state: MarketDataState, provider_id: str, quote: Quote, ts: float
    ) -> MarketDataState:

        evt = QuoteReceived(MarketDataEngine._create_id(), ts, provider_id, quote.symbol)
        return replace(
            state,
            quotes=state.quotes.set(quote.symbol, quote),
            events=state.events.append(evt),
        )

    @staticmethod
    def process_trade(
        state: MarketDataState, provider_id: str, trade: Trade, ts: float
    ) -> MarketDataState:

        evt = TradeReceived(MarketDataEngine._create_id(), ts, provider_id, trade.symbol)
        return replace(
            state,
            trades=state.trades.set(trade.symbol, trade),
            events=state.events.append(evt),
        )

    @staticmethod
    def process_bar(
        state: MarketDataState, provider_id: str, bar: Bar, ts: float
    ) -> MarketDataState:

        evt = BarReceived(MarketDataEngine._create_id(), ts, provider_id, bar.symbol)
        return replace(
            state,
            bars=state.bars.set(bar.symbol, bar),
            events=state.events.append(evt),
        )

    @staticmethod
    def process_order_book(
        state: MarketDataState, provider_id: str, ob: OrderBook, ts: float
    ) -> MarketDataState:
        evt = OrderBookUpdated(MarketDataEngine._create_id(), ts, provider_id, ob.symbol)
        return replace(
            state,
            order_books=state.order_books.set(ob.symbol, ob),
            events=state.events.append(evt),
        )

    @staticmethod
    def latest_quote(state: MarketDataState, symbol: str) -> Quote | None:
        return state.quotes.get(symbol)

    @staticmethod
    def latest_trade(state: MarketDataState, symbol: str) -> Trade | None:
        return state.trades.get(symbol)

    @staticmethod
    def latest_bar(state: MarketDataState, symbol: str) -> Bar | None:
        return state.bars.get(symbol)

    @staticmethod
    def order_book(state: MarketDataState, symbol: str) -> OrderBook | None:
        return state.order_books.get(symbol)
