"""Deterministic algorithms handling bad data boundaries."""

from alphalab.data.feed import Bar


def remove_duplicates(bars: tuple[Bar, ...]) -> tuple[Bar, ...]:
    """Preserves the first instance of a duplicate timestamp."""
    seen = set()
    cleaned = []
    for b in bars:
        if b.timestamp not in seen:
            cleaned.append(b)
            seen.add(b.timestamp)
    return tuple(cleaned)

def remove_invalid_ohlc(bars: tuple[Bar, ...]) -> tuple[Bar, ...]:
    """Drops bars that mathematically make no sense."""
    return tuple(
        b for b in bars 
        if b.high >= b.low and b.open >= 0 and b.high >= 0 and b.low >= 0 and b.close >= 0
    )