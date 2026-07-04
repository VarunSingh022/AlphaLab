"""Automatic column detection dictionaries."""

from collections.abc import Mapping

# Common alias maps universally standardizing headers
COLUMN_ALIASES: Mapping[str, str] = {
    "date": "timestamp",
    "datetime": "timestamp",
    "time": "timestamp",
    "t": "timestamp",
    
    "open": "open",
    "o": "open",
    
    "high": "high",
    "h": "high",
    
    "low": "low",
    "l": "low",
    
    "close": "close",
    "c": "close",
    "adj close": "close",
    "price": "close",
    "last": "close",
    
    "volume": "volume",
    "vol": "volume",
    "v": "volume",
    
    "ticker": "symbol",
    "sym": "symbol",
    "security": "symbol",
    "asset": "symbol",
}