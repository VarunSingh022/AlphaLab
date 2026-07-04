"""Adapter translating AlphaLab requests to canonical structures."""

from typing import Any

from alphalab.marketdata.feed import Trade


class MarketDataAdapter:
    """Stateless translator ensuring downstream engine compatibility."""
    
    @staticmethod
    def to_oms_tick(trade: Trade) -> dict[str, Any]:
        return {
            "symbol": trade.symbol,
            "price": trade.price,
            "quantity": trade.size,
            "timestamp": trade.timestamp
        }