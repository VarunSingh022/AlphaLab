"""AlphaLab Alternative Data Engine.

News sentiment, aggregated sentiment, satellite-derived metrics, shipping/AIS
metrics, ESG scoring, and credit card spending panels -- all with point-in-time
correctness via `alphalab.common.point_in_time.known_as_of` and explicit
`DataProvenance` tracking, since alt-data vendor quality and coverage varies far
more than official statistics. `feature_bridge` connects every category into
Feature Store the same way `alphalab.factor_library` does.
"""

from alphalab.alt_data.credit_card import CreditCardSpendingObservation
from alphalab.alt_data.esg import ESGScore, composite_score
from alphalab.alt_data.exceptions import AltDataError, AltDataInputError
from alphalab.alt_data.feature_bridge import (
    AltDataFeatureValue,
    from_aggregated_sentiment,
    from_credit_card_observation,
    from_esg_score,
    from_news_sentiment,
    from_satellite_observation,
    from_shipping_observation,
)
from alphalab.alt_data.news import NewsSentiment
from alphalab.alt_data.provenance import DataProvenance
from alphalab.alt_data.satellite import SatelliteMetricType, SatelliteObservation
from alphalab.alt_data.sentiment import AggregatedSentiment, aggregate_from_news
from alphalab.alt_data.shipping import ShippingMetricType, ShippingObservation

__all__ = [
    "AggregatedSentiment",
    "AltDataError",
    "AltDataFeatureValue",
    "AltDataInputError",
    "CreditCardSpendingObservation",
    "DataProvenance",
    "ESGScore",
    "NewsSentiment",
    "SatelliteMetricType",
    "SatelliteObservation",
    "ShippingMetricType",
    "ShippingObservation",
    "aggregate_from_news",
    "composite_score",
    "from_aggregated_sentiment",
    "from_credit_card_observation",
    "from_esg_score",
    "from_news_sentiment",
    "from_satellite_observation",
    "from_shipping_observation",
]
