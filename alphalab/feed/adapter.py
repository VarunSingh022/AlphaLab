"""Adapter connecting normalization rules to the Feed Layer."""

from typing import Any

from alphalab.feed.normalization import (
    RawPayload,
    normalize_bar,
    normalize_book,
    normalize_quote,
    normalize_tick,
)


class FeedAdapter:
    """Stateless translator from raw provider packets to normalized Market data."""

    @staticmethod
    def process_payload(payload: RawPayload, provider_name: str) -> Any:
        """Routes payloads to correct normalizers based on type tag."""
        ptype = payload.payload_type.upper()
        
        if ptype == "TICK":
            return normalize_tick(payload, provider_name)
        elif ptype == "QUOTE":
            return normalize_quote(payload, provider_name)
        elif ptype == "BAR":
            return normalize_bar(payload)
        elif ptype == "BOOK":
            return normalize_book(payload)
            
        raise ValueError(f"Unknown payload type: {ptype}")