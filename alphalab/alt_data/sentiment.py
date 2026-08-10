"""Aggregated sentiment over a time window, built from individual news articles.

Distinct from `alphalab.alt_data.news.NewsSentiment`: a single article score is a
raw observation, while this represents a windowed aggregate signal -- the shape
most sentiment-index vendors (and most strategies) actually consume.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.news import NewsSentiment
from alphalab.alt_data.provenance import DataProvenance


@dataclass(frozen=True, slots=True)
class AggregatedSentiment:
    """A mean sentiment score across every article for one asset in a time window.

    Attributes:
        asset_id: Identifier of the asset this aggregate concerns.
        window_start: Unix timestamp the aggregation window begins.
        window_end: Unix timestamp the aggregation window ends.
        release_date: Unix timestamp this aggregate became available. Since it
            depends on every contributing article's own score, this is the latest
            of those articles' release_dates, not simply window_end.
        mean_sentiment: Average sentiment_score across contributing articles.
        volume: Number of articles contributing to this aggregate.
        source: Vendor and reliability information.
    """

    asset_id: str
    window_start: float
    window_end: float
    release_date: float
    mean_sentiment: Decimal
    volume: int
    source: DataProvenance

    def __post_init__(self) -> None:
        if self.window_end <= self.window_start:
            raise AltDataInputError("window_end must be after window_start.")
        if self.volume < 0:
            raise AltDataInputError(f"volume cannot be negative, got {self.volume}.")
        if not (Decimal("-1") <= self.mean_sentiment <= Decimal("1")):
            raise AltDataInputError(
                f"mean_sentiment must be between -1 and 1, got {self.mean_sentiment}."
            )

    @property
    def reference_period(self) -> float:
        """Alias for `window_end`, satisfying PointInTimeRecord."""
        return self.window_end


def aggregate_from_news(
    news_items: tuple[NewsSentiment, ...],
    asset_id: str,
    window_start: float,
    window_end: float,
    source: DataProvenance,
) -> AggregatedSentiment:
    """Builds an AggregatedSentiment from articles published within a window.

    Raises:
        AltDataInputError: If no articles for asset_id fall within
            [window_start, window_end].
    """
    matching = tuple(
        item
        for item in news_items
        if item.asset_id == asset_id and window_start <= item.published_at <= window_end
    )
    if not matching:
        raise AltDataInputError(
            f"No news items for '{asset_id}' fall within the requested window; "
            "cannot aggregate zero articles into a meaningful score."
        )

    mean_sentiment = sum((item.sentiment_score for item in matching), Decimal("0")) / len(matching)
    release_date = max(item.release_date for item in matching)

    return AggregatedSentiment(
        asset_id=asset_id,
        window_start=window_start,
        window_end=window_end,
        release_date=release_date,
        mean_sentiment=mean_sentiment,
        volume=len(matching),
        source=source,
    )
