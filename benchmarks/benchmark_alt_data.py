"""High-performance benchmark suite for the stateless Alternative Data Engine."""

import time
from decimal import Decimal

from alphalab.alt_data import (
    DataProvenance,
    ESGScore,
    NewsSentiment,
    aggregate_from_news,
    composite_score,
    from_news_sentiment,
)
from alphalab.common.point_in_time import known_as_of


def run_benchmark() -> None:
    source = DataProvenance(vendor="Bench", coverage_description="bench", confidence=Decimal("0.8"))
    articles = tuple(
        NewsSentiment(
            asset_id="AAPL",
            headline=f"Headline {i}",
            published_at=float(i * 60),
            release_date=float(i * 60 + 30),
            sentiment_score=Decimal("0.1") * (i % 10 - 5),
            source=source,
        )
        for i in range(60)
    )
    esg = ESGScore(
        asset_id="AAPL",
        reference_period=0.0,
        release_date=10.0,
        environmental_score=Decimal("80"),
        social_score=Decimal("60"),
        governance_score=Decimal("70"),
        source=source,
    )

    N = 100_000
    print(f"Starting Alternative Data Engine Benchmark: {N} computations per operation...")

    start = time.perf_counter()
    for _ in range(N):
        known_as_of(articles, as_of=10000.0)
    duration = time.perf_counter() - start
    print(f"  known_as_of (60 articles): {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        aggregate_from_news(
            articles, asset_id="AAPL", window_start=0.0, window_end=10000.0, source=source
        )
    duration = time.perf_counter() - start
    print(f"  aggregate_from_news       : {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        composite_score(esg)
    duration = time.perf_counter() - start
    print(f"  composite_score           : {duration:.4f}s total, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        from_news_sentiment(articles[0], feature_id="news_sentiment", version=1)
    duration = time.perf_counter() - start
    print(f"  from_news_sentiment       : {duration:.4f}s total, {N / duration:.2f} ops/sec")


if __name__ == "__main__":
    run_benchmark()
