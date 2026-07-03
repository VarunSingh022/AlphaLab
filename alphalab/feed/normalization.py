"""Translates external provider payloads into pure AlphaLab Market Engine models."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from alphalab.market import Bar, OrderBookLevel, OrderBookSnapshot, Quote, Tick, TimeFrame


@dataclass(frozen=True, slots=True)
class RawPayload:
    """Generic carrier for external provider-specific data dictionaries."""
    payload_type: str
    data: Mapping[str, Any]


def normalize_tick(payload: RawPayload, provider: str) -> Tick:
    """Maps raw trade prints to an immutable AlphaLab Tick."""
    d = payload.data
    return Tick(
        asset_id=str(d["symbol"]),
        timestamp=float(d["ts"]),
        price=Decimal(str(d["price"])),
        quantity=Decimal(str(d["size"])),
        trade_id=str(d["id"]),
        venue=provider,
        currency="USD",
    )


def normalize_quote(payload: RawPayload, provider: str) -> Quote:
    """Maps raw top-of-book to an immutable AlphaLab Quote."""
    d = payload.data
    return Quote(
        asset_id=str(d["symbol"]),
        timestamp=float(d["ts"]),
        bid=Decimal(str(d["bid"])),
        ask=Decimal(str(d["ask"])),
        bid_size=Decimal(str(d["bid_size"])),
        ask_size=Decimal(str(d["ask_size"])),
        venue=provider,
        currency="USD",
    )


def normalize_bar(payload: RawPayload) -> Bar:
    """Maps raw OHLCV to an immutable AlphaLab Bar."""
    d = payload.data
    
    tf_str = str(d.get("timeframe", "1m"))
    tf = TimeFrame.M1
    for enum_val in TimeFrame:
        if enum_val.value == tf_str:
            tf = enum_val
            break

    return Bar(
        asset_id=str(d["symbol"]),
        timestamp=float(d["ts"]),
        open=Decimal(str(d["open"])),
        high=Decimal(str(d["high"])),
        low=Decimal(str(d["low"])),
        close=Decimal(str(d["close"])),
        volume=Decimal(str(d["volume"])),
        vwap=Decimal(str(d.get("vwap", "0.0"))),
        trade_count=int(d.get("trade_count", 0)),
        timeframe=tf,
    )


def normalize_book(payload: RawPayload) -> OrderBookSnapshot:
    """Maps a raw depth representation to an immutable OrderBookSnapshot."""
    d = payload.data
    
    bids = tuple(
        OrderBookLevel(price=Decimal(str(p)), size=Decimal(str(s)), orders=int(o))
        for p, s, o in d.get("bids", [])
    )
    
    asks = tuple(
        OrderBookLevel(price=Decimal(str(p)), size=Decimal(str(s)), orders=int(o))
        for p, s, o in d.get("asks", [])
    )
    
    return OrderBookSnapshot(
        asset_id=str(d["symbol"]),
        timestamp=float(d["ts"]),
        bids=bids,
        asks=asks,
        sequence=int(d.get("sequence", 1)),
    )