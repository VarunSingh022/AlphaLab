"""Adapters converting Live Market messages to standard AlphaLab abstractions."""

from collections.abc import Mapping
from typing import Any

from alphalab.live.message import MarketMessage, QuoteTick, TradeTick


class LiveAdapter:
    """Stateless translator formatting Live payloads for downstream engines."""

    @staticmethod
    def to_market_tick(trade: TradeTick) -> Mapping[str, Any]:
        """Converts a live TradeTick into a generic dict readable by the Market Engine."""
        return {
            "asset_id": trade.symbol,
            "timestamp": trade.timestamp,
            "price": trade.price,
            "quantity": trade.size,
            "venue": trade.provider_id,
        }

    @staticmethod
    def to_market_quote(quote: QuoteTick) -> Mapping[str, Any]:
        """Converts a live QuoteTick into a generic dict readable by the Market Engine."""
        return {
            "asset_id": quote.symbol,
            "timestamp": quote.timestamp,
            "bid": quote.bid,
            "ask": quote.ask,
            "bid_size": quote.bid_size,
            "ask_size": quote.ask_size,
            "venue": quote.provider_id,
        }

    @staticmethod
    def format_event(message: MarketMessage) -> Mapping[str, Any]:
        """Generic fallback wrapper for standard event buses."""
        return {
            "source": "LIVE",
            "provider_id": message.provider_id,
            "timestamp": message.timestamp,
            "payload_type": type(message).__name__,
        }
