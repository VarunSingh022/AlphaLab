"""AlphaLab Alternative Data Engine: external information, point in time.

Two layers, sharing one package because they share one question -- *what did
anyone know, and when?*

* **Typed v1 categories** -- news sentiment, aggregated sentiment,
  satellite-derived metrics, shipping/AIS metrics, ESG scoring and credit card
  spending panels, each with a reference period, a release date and a
  :class:`DataProvenance`, queried through
  :func:`alphalab.common.point_in_time.known_as_of`. ``feature_bridge``
  connects each into Feature Store. Unchanged.
* **The v3.7 point-in-time foundation** -- the extensible form every kind of
  external information takes when research must be able to say when it was
  knowable:

  ======================== ==================================================
  Generic observations     :class:`ExternalObservation` -- any category, any
                           metric, open vocabulary
  Information events       :class:`InformationEvent` -- earnings, macro
                           releases, corporate actions, news, sentiment
  Fundamentals             :class:`FundamentalObservation`, statements,
                           trailing twelve months, valuation, ratios,
                           restatements
  Source identity          :class:`ObservationSource` -- which source, which
                           version, which bytes
  Versioned sets           :class:`ObservationSet` -- derived identity,
                           lineage, vintage checks
  Point-in-time reads      :class:`ObservationView` -- what was visible at an
                           instant, under which clock, at which revision
  Sessions                 :func:`place_in_session` -- when information
                           known at an instant can first be traded
  ======================== ==================================================

Every v3.7 record carries a
:class:`~alphalab.common.point_in_time.PointInTimeStamp`: when it describes,
when it became knowable and how that was established, when it takes effect,
and when it arrived. A record whose availability nobody established is never
visible to research.

The package is a leaf over :mod:`alphalab.common`. It reaches a market calendar
through a structural protocol and a source's bytes through their digest, so the
feature, research and application layers can all read it without closing a
cycle. It ships no data, names no vendor, and fetches nothing.
"""

from alphalab.alt_data.credit_card import CreditCardSpendingObservation
from alphalab.alt_data.esg import ESGScore, composite_score
from alphalab.alt_data.exceptions import AltDataError, AltDataInputError, PointInTimeError
from alphalab.alt_data.feature_bridge import (
    AltDataFeatureValue,
    from_aggregated_sentiment,
    from_credit_card_observation,
    from_esg_score,
    from_news_sentiment,
    from_satellite_observation,
    from_shipping_observation,
)
from alphalab.alt_data.fundamentals import (
    BOOK_EQUITY,
    CASH,
    CURRENT_ASSETS,
    CURRENT_LIABILITIES,
    DIVIDENDS_PER_SHARE,
    EARNINGS,
    EARNINGS_PER_SHARE,
    EBITDA,
    FREE_CASH_FLOW,
    FUNDAMENTAL_KEY_SCHEME,
    GROSS_PROFIT,
    OPERATING_INCOME,
    REVENUE,
    SHARES_OUTSTANDING,
    TOTAL_ASSETS,
    TOTAL_DEBT,
    AggregateStep,
    Aggregation,
    FinancialRatios,
    FinancialStatement,
    FiscalPeriod,
    FundamentalInput,
    FundamentalInputs,
    FundamentalObservation,
    GrowthValue,
    Restatement,
    StatementType,
    TrailingValue,
    ValuationMetrics,
    aggregate_timeline,
    canonical_fundamental_key,
    currency_of_per_share_unit,
    financial_ratios,
    fundamental_as_of,
    fundamental_inputs_as_of,
    latest_fundamental,
    restatements,
    statement_as_of,
    trailing_twelve_months,
    valuation_metrics,
    year_over_year_growth,
)
from alphalab.alt_data.information import (
    INFORMATION_EVENT_KEY_SCHEME,
    InformationEvent,
    canonical_event_key,
    restrict_measured,
    surprise,
)
from alphalab.alt_data.news import NewsSentiment
from alphalab.alt_data.observation import (
    OBSERVATION_KEY_SCHEME,
    ExternalObservation,
    ReferencePeriod,
    canonical_observation_key,
    observation_from_release,
)
from alphalab.alt_data.observation_set import (
    OBSERVATION_SET_KEY_SCHEME,
    ObservationSet,
    ObservationView,
    PointInTimeSelection,
    SetRecord,
    SetTransformation,
    VintagePolicy,
    build_observation_set,
    canonical_set_key,
    derive_set_version,
    known_by,
    latest_vintages,
    original_vintages,
    originals_only,
    restrict_observed,
    restrict_subjects,
    verify_observation_set,
)
from alphalab.alt_data.provenance import DataProvenance
from alphalab.alt_data.satellite import SatelliteMetricType, SatelliteObservation
from alphalab.alt_data.sentiment import AggregatedSentiment, aggregate_from_news
from alphalab.alt_data.sessions import (
    SessionCalendar,
    SessionPlacement,
    SessionTiming,
    place_in_session,
    place_record,
)
from alphalab.alt_data.shipping import ShippingMetricType, ShippingObservation
from alphalab.alt_data.source import ObservationSource

__all__ = [
    "BOOK_EQUITY",
    "CASH",
    "CURRENT_ASSETS",
    "CURRENT_LIABILITIES",
    "DIVIDENDS_PER_SHARE",
    "EARNINGS",
    "EARNINGS_PER_SHARE",
    "EBITDA",
    "FREE_CASH_FLOW",
    "FUNDAMENTAL_KEY_SCHEME",
    "GROSS_PROFIT",
    "INFORMATION_EVENT_KEY_SCHEME",
    "OBSERVATION_KEY_SCHEME",
    "OBSERVATION_SET_KEY_SCHEME",
    "OPERATING_INCOME",
    "REVENUE",
    "SHARES_OUTSTANDING",
    "TOTAL_ASSETS",
    "TOTAL_DEBT",
    "AggregateStep",
    "AggregatedSentiment",
    "Aggregation",
    "AltDataError",
    "AltDataFeatureValue",
    "AltDataInputError",
    "CreditCardSpendingObservation",
    "DataProvenance",
    "ESGScore",
    "ExternalObservation",
    "FinancialRatios",
    "FinancialStatement",
    "FiscalPeriod",
    "FundamentalInput",
    "FundamentalInputs",
    "FundamentalObservation",
    "GrowthValue",
    "InformationEvent",
    "NewsSentiment",
    "ObservationSet",
    "ObservationSource",
    "ObservationView",
    "PointInTimeError",
    "PointInTimeSelection",
    "ReferencePeriod",
    "Restatement",
    "SatelliteMetricType",
    "SatelliteObservation",
    "SessionCalendar",
    "SessionPlacement",
    "SessionTiming",
    "SetRecord",
    "SetTransformation",
    "ShippingMetricType",
    "ShippingObservation",
    "StatementType",
    "TrailingValue",
    "ValuationMetrics",
    "VintagePolicy",
    "aggregate_from_news",
    "aggregate_timeline",
    "build_observation_set",
    "canonical_event_key",
    "canonical_fundamental_key",
    "canonical_observation_key",
    "canonical_set_key",
    "composite_score",
    "currency_of_per_share_unit",
    "derive_set_version",
    "financial_ratios",
    "from_aggregated_sentiment",
    "from_credit_card_observation",
    "from_esg_score",
    "from_news_sentiment",
    "from_satellite_observation",
    "from_shipping_observation",
    "fundamental_as_of",
    "fundamental_inputs_as_of",
    "known_by",
    "latest_fundamental",
    "latest_vintages",
    "observation_from_release",
    "original_vintages",
    "originals_only",
    "place_in_session",
    "place_record",
    "restatements",
    "restrict_measured",
    "restrict_observed",
    "restrict_subjects",
    "statement_as_of",
    "surprise",
    "trailing_twelve_months",
    "valuation_metrics",
    "verify_observation_set",
    "year_over_year_growth",
]
