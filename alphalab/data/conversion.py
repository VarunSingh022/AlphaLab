"""Data transformations including pure Python resampling."""

from alphalab.data.feed import Bar


def resample_bars(bars: tuple[Bar, ...], interval_seconds: float) -> tuple[Bar, ...]:
    """Aggregates smaller timeframe bars into larger buckets purely functionally."""
    if not bars or interval_seconds <= 0:
        return ()

    groups: dict[float, list[Bar]] = {}
    for b in bars:
        bucket = float((b.timestamp // interval_seconds) * interval_seconds)
        if bucket not in groups:
            groups[bucket] = []
        groups[bucket].append(b)
        
    resampled = []
    for bucket in sorted(groups.keys()):
        grp = groups[bucket]
        r_open = grp[0].open
        r_high = max(b.high for b in grp)
        r_low = min(b.low for b in grp)
        r_close = grp[-1].close
        r_vol = sum(b.volume for b in grp)
        
        resampled.append(
            Bar(grp[0].symbol, bucket, r_open, r_high, r_low, r_close, r_vol)
        )
        
    return tuple(resampled)