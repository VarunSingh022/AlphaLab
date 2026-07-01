"""Pure functional Market Data engine."""

import uuid
from dataclasses import replace

from alphalab.market.bar import Bar
from alphalab.market.events import (
    BarClosed,
    BookUpdated,
    QuoteReceived,
    SnapshotCreated,
    TickReceived,
)
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.quote import Quote
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.state import MarketState
from alphalab.market.tick import Tick
from alphalab.market.validation import (
    validate_bar,
    validate_quote,
    validate_snapshot,
    validate_tick,
)


class MarketEngine:
    """Stateless functional market data routing and distribution engine."""

    @staticmethod
    def _create_event_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def reset() -> MarketState:
        """Returns a completely fresh state."""
        return MarketState()

    @staticmethod
    def clear(state: MarketState) -> MarketState:
        """Returns a new state with cleared market caches."""
        return MarketState(events=state.events)

    @staticmethod
    def publish_tick(state: MarketState, tick: Tick) -> MarketState:
        """Validates and processes an incoming tick."""
        validate_tick(tick)

        event = TickReceived(
            event_id=MarketEngine._create_event_id(),
            timestamp=tick.timestamp,
            tick=tick,
        )

        new_ticks = dict(state.latest_ticks)
        new_ticks[tick.asset_id] = tick

        return replace(
            state,
            latest_ticks=new_ticks,
            history=(*state.history, event),
            events=(*state.events, event),
        )

    @staticmethod
    def publish_quote(state: MarketState, quote: Quote) -> MarketState:
        """Validates and processes an incoming Top of Book quote."""
        validate_quote(quote)

        event = QuoteReceived(
            event_id=MarketEngine._create_event_id(),
            timestamp=quote.timestamp,
            quote=quote,
        )

        new_quotes = dict(state.latest_quotes)
        new_quotes[quote.asset_id] = quote

        return replace(
            state,
            latest_quotes=new_quotes,
            history=(*state.history, event),
            events=(*state.events, event),
        )

    @staticmethod
    def publish_bar(state: MarketState, bar: Bar) -> MarketState:
        """Validates and processes a completed OHLCV bar."""
        validate_bar(bar)

        event = BarClosed(
            event_id=MarketEngine._create_event_id(),
            timestamp=bar.timestamp,
            bar=bar,
        )

        new_bars = dict(state.latest_bars)
        new_bars[f"{bar.asset_id}_{bar.timeframe.value}"] = bar

        return replace(
            state,
            latest_bars=new_bars,
            history=(*state.history, event),
            events=(*state.events, event),
        )

    @staticmethod
    def publish_book(state: MarketState, book: OrderBookSnapshot) -> MarketState:
        """Validates and processes a depth of book update."""
        validate_snapshot(book)

        if book.asset_id in state.latest_books:
            last_seq = state.latest_books[book.asset_id].sequence
            if book.sequence <= last_seq:
                raise MarketValidationError(
                    f"Duplicate or backward sequence. Last: {last_seq}, Current: {book.sequence}"
                )

        event = BookUpdated(
            event_id=MarketEngine._create_event_id(),
            timestamp=book.timestamp,
            snapshot=book,
        )

        new_books = dict(state.latest_books)
        new_books[book.asset_id] = book

        return replace(
            state,
            latest_books=new_books,
            history=(*state.history, event),
            events=(*state.events, event),
        )

    @staticmethod
    def publish_snapshot(state: MarketState, snapshot: OrderBookSnapshot) -> MarketState:
        """Validates and publishes a primary snapshot generation event."""
        validate_snapshot(snapshot)

        event = SnapshotCreated(
            event_id=MarketEngine._create_event_id(),
            timestamp=snapshot.timestamp,
            snapshot=snapshot,
        )

        new_books = dict(state.latest_books)
        new_books[snapshot.asset_id] = snapshot

        return replace(
            state,
            latest_books=new_books,
            history=(*state.history, event),
            events=(*state.events, event),
        )
