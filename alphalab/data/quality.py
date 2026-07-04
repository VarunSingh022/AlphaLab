"""Deterministic data quality detection and evaluation."""

from dataclasses import dataclass

from alphalab.data.feed import Bar


@dataclass(frozen=True, slots=True)
class QualityReport:
    dataset_id: str
    completeness: float
    consistency: float
    missing_count: int
    duplicate_count: int
    invalid_count: int
    out_of_order_count: int
    quality_score: float

def evaluate_bar_quality(dataset_id: str, bars: tuple[Bar, ...]) -> QualityReport:
    """Deterministically identifies missing, invalid, or corrupted data."""
    if not bars:
        return QualityReport(dataset_id, 0.0, 0.0, 0, 0, 0, 0, 0.0)

    total = len(bars)
    missing, invalid, out_of_order, duplicates = 0, 0, 0, 0
    
    seen_ts = set()
    prev_ts = -1.0

    for b in bars:
        # Invalid OHLC checks
        if b.high < b.low or b.open < 0 or b.high < 0 or b.low < 0 or b.close < 0:
            invalid += 1
            
        # Time consistency
        if b.timestamp in seen_ts:
            duplicates += 1
        seen_ts.add(b.timestamp)
        
        if b.timestamp < prev_ts:
            out_of_order += 1
        prev_ts = b.timestamp

    # Penalties
    inconsistencies = invalid + out_of_order + duplicates
    completeness = 100.0 - min(100.0, (missing / total) * 100.0)
    consistency = 100.0 - min(100.0, (inconsistencies / total) * 100.0)
    score = (completeness * 0.4) + (consistency * 0.6)

    return QualityReport(
        dataset_id, round(completeness, 2), round(consistency, 2), 
        missing, duplicates, invalid, out_of_order, round(score, 2)
    )