"""Per-article news sentiment.

`release_date` can meaningfully lag `published_at`: an article's publication time
is not the same moment its sentiment score becomes available, since NLP scoring
takes real processing time. Exposes `reference_period` as an alias for
`published_at` so this type satisfies `alphalab.common.point_in_time.PointInTimeRecord`
without renaming the more readable field name.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.provenance import DataProvenance


@dataclass(frozen=True, slots=True)
class NewsSentiment:
    """A single article's sentiment score for one asset.

    Attributes:
        asset_id: Identifier of the asset this article concerns.
        headline: The article's headline.
        published_at: Unix timestamp of publication.
        release_date: Unix timestamp the sentiment score became available. Must be
            on or after published_at.
        sentiment_score: Score from -1 (most negative) to 1 (most positive).
        source: Vendor and reliability information.
    """

    asset_id: str
    headline: str
    published_at: float
    release_date: float
    sentiment_score: Decimal
    source: DataProvenance

    def __post_init__(self) -> None:
        if not (Decimal("-1") <= self.sentiment_score <= Decimal("1")):
            raise AltDataInputError(
                f"sentiment_score must be between -1 and 1, got {self.sentiment_score}."
            )
        if self.release_date < self.published_at:
            raise AltDataInputError("release_date cannot be before published_at.")

    @property
    def reference_period(self) -> float:
        """Alias for `published_at`, satisfying PointInTimeRecord."""
        return self.published_at
