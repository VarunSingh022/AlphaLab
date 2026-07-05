"""Canonical normalizations applied post-parsing."""

from alphalab.data.feed import Bar


def normalize_prices(bars: tuple[Bar, ...], factor: float) -> tuple[Bar, ...]:
    """Scales prices uniformly (e.g., currency conversion or split application)."""
    return tuple(
        Bar(
            b.symbol,
            b.timestamp,
            b.open * factor,
            b.high * factor,
            b.low * factor,
            b.close * factor,
            b.volume,
        )
        for b in bars
    )
