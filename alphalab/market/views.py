"""Pure queries to expose transparent Market Data access."""

from collections.abc import Sequence

from alphalab.market.bar import Bar
from alphalab.market.quote import Quote
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.state import MarketState
from alphalab.market.tick import Tick


def latest_quote(state: MarketState, asset_id: str) -> Quote | None:
    return state.latest_quotes.get(asset_id)


def latest_tick(state: MarketState, asset_id: str) -> Tick | None:
    return state.latest_ticks.get(asset_id)


def latest_book(state: MarketState, asset_id: str) -> OrderBookSnapshot | None:
    return state.latest_books.get(asset_id)


def latest_bar(state: MarketState, asset_id: str, timeframe: str) -> Bar | None:
    return state.latest_bars.get(f"{asset_id}_{timeframe}")


def quotes(state: MarketState) -> Sequence[Quote]:
    return tuple(state.latest_quotes.values())


def ticks(state: MarketState) -> Sequence[Tick]:
    return tuple(state.latest_ticks.values())


def books(state: MarketState) -> Sequence[OrderBookSnapshot]:
    return tuple(state.latest_books.values())


def bars(state: MarketState) -> Sequence[Bar]:
    return tuple(state.latest_bars.values())
