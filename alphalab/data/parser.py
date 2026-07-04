"""Pure functional mapping of raw dictionaries using standard aliases."""

from collections.abc import Mapping, Sequence
from typing import Any

from alphalab.data.feed import Bar
from alphalab.data.formats import COLUMN_ALIASES


def parse_raw_rows(symbol: str, raw_rows: Sequence[Mapping[str, Any]]) -> tuple[Bar, ...]:
    """Translates dynamically keyed external data into normalized Canonical Bars."""
    parsed = []
    
    for row in raw_rows:
        norm_row = {}
        for k, v in row.items():
            norm_key = COLUMN_ALIASES.get(str(k).strip().lower(), str(k).strip().lower())
            norm_row[norm_key] = v
            
        try:
            bar = Bar(
                symbol=norm_row.get("symbol", symbol),
                timestamp=float(norm_row["timestamp"]),
                open=float(norm_row["open"]),
                high=float(norm_row["high"]),
                low=float(norm_row["low"]),
                close=float(norm_row["close"]),
                volume=float(norm_row.get("volume", 0.0))
            )
            parsed.append(bar)
        except (KeyError, ValueError, TypeError):
            # Drops rows that completely fail structural translation
            continue
            
    # Guarantee chronological order
    parsed.sort(key=lambda b: b.timestamp)
    return tuple(parsed)