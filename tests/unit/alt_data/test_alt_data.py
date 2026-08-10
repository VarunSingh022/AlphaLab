"""Comprehensive tests for the Alternative Data Engine: all six categories, provenance,
point-in-time correctness, and the Feature Store bridge."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from alphalab.alt_data import (
    AggregatedSentiment,
    AltDataInputError,
    CreditCardSpendingObservation,
    DataProvenance,
    ESGScore,
    NewsSentiment,
    SatelliteMetricType,
    SatelliteObservation,
    ShippingMetricType,
    ShippingObservation,
    aggregate_from_news,
    composite_score,
    from_aggregated_sentiment,
    from_credit_card_observation,
    from_esg_score,
    from_news_sentiment,
    from_satellite_observation,
    from_shipping_observation,
)
from alphalab.common.point_in_time import known_as_of
from alphalab.feature_store import FeatureValueAdapter, FeatureValueProtocol


def _source(confidence: str = "0.8") -> DataProvenance:
    return DataProvenance(
        vendor="TestVendor", coverage_description="test coverage", confidence=Decimal(confidence)
    )


# --------------------------------------------------------------------------- #
# DataProvenance
# --------------------------------------------------------------------------- #


def test_provenance_is_immutable() -> None:
    source = _source()
    with pytest.raises(FrozenInstanceError):
        source.vendor = "Other"  # type: ignore[misc]


def test_provenance_rejects_confidence_above_one() -> None:
    with pytest.raises(AltDataInputError):
        DataProvenance(vendor="X", coverage_description="x", confidence=Decimal("1.5"))


def test_provenance_rejects_negative_confidence() -> None:
    with pytest.raises(AltDataInputError):
        DataProvenance(vendor="X", coverage_description="x", confidence=Decimal("-0.1"))


def test_provenance_allows_boundary_values() -> None:
    DataProvenance(vendor="X", coverage_description="x", confidence=Decimal("0"))
    DataProvenance(vendor="X", coverage_description="x", confidence=Decimal("1"))


# --------------------------------------------------------------------------- #
# NewsSentiment
# --------------------------------------------------------------------------- #


def test_news_sentiment_rejects_out_of_range_score() -> None:
    with pytest.raises(AltDataInputError):
        NewsSentiment(
            asset_id="AAPL",
            headline="H",
            published_at=0.0,
            release_date=10.0,
            sentiment_score=Decimal("1.5"),
            source=_source(),
        )


def test_news_sentiment_rejects_release_before_publication() -> None:
    with pytest.raises(AltDataInputError):
        NewsSentiment(
            asset_id="AAPL",
            headline="H",
            published_at=100.0,
            release_date=50.0,
            sentiment_score=Decimal("0.5"),
            source=_source(),
        )


def test_news_sentiment_reference_period_aliases_published_at() -> None:
    item = NewsSentiment(
        asset_id="AAPL",
        headline="H",
        published_at=100.0,
        release_date=110.0,
        sentiment_score=Decimal("0.5"),
        source=_source(),
    )
    assert item.reference_period == item.published_at


def test_news_sentiment_satisfies_generic_known_as_of() -> None:
    """Cross-package proof: NewsSentiment works with common.point_in_time.known_as_of
    with zero adapter code, purely via structural typing."""
    a = NewsSentiment(
        asset_id="AAPL",
        headline="A",
        published_at=100.0,
        release_date=110.0,
        sentiment_score=Decimal("0.5"),
        source=_source(),
    )
    b = NewsSentiment(
        asset_id="AAPL",
        headline="B",
        published_at=200.0,
        release_date=210.0,
        sentiment_score=Decimal("-0.2"),
        source=_source(),
    )
    result = known_as_of((a, b), as_of=150.0)
    assert result is a


# --------------------------------------------------------------------------- #
# AggregatedSentiment / aggregate_from_news
# --------------------------------------------------------------------------- #


def _two_articles() -> tuple[NewsSentiment, NewsSentiment]:
    a = NewsSentiment(
        asset_id="AAPL",
        headline="A",
        published_at=100.0,
        release_date=110.0,
        sentiment_score=Decimal("0.5"),
        source=_source(),
    )
    b = NewsSentiment(
        asset_id="AAPL",
        headline="B",
        published_at=200.0,
        release_date=210.0,
        sentiment_score=Decimal("-0.2"),
        source=_source(),
    )
    return a, b


def test_aggregate_from_news_computes_mean() -> None:
    a, b = _two_articles()
    agg = aggregate_from_news(
        (a, b), asset_id="AAPL", window_start=0.0, window_end=300.0, source=_source()
    )
    assert agg.mean_sentiment == Decimal("0.15")
    assert agg.volume == 2


def test_aggregate_from_news_release_date_is_latest_contributor() -> None:
    """The aggregate can't be known until the last contributing article scores --
    release_date must be the max, not window_end."""
    a, b = _two_articles()
    agg = aggregate_from_news(
        (a, b), asset_id="AAPL", window_start=0.0, window_end=300.0, source=_source()
    )
    assert agg.release_date == 210.0


def test_aggregate_from_news_excludes_out_of_window_articles() -> None:
    a, b = _two_articles()
    agg = aggregate_from_news(
        (a, b), asset_id="AAPL", window_start=0.0, window_end=150.0, source=_source()
    )
    assert agg.volume == 1
    assert agg.mean_sentiment == Decimal("0.5")


def test_aggregate_from_news_excludes_other_assets() -> None:
    a, b = _two_articles()
    c = NewsSentiment(
        asset_id="MSFT",
        headline="C",
        published_at=150.0,
        release_date=160.0,
        sentiment_score=Decimal("0.9"),
        source=_source(),
    )
    agg = aggregate_from_news(
        (a, b, c), asset_id="AAPL", window_start=0.0, window_end=300.0, source=_source()
    )
    assert agg.volume == 2


def test_aggregate_from_news_raises_on_zero_matching_articles() -> None:
    a, b = _two_articles()
    with pytest.raises(AltDataInputError):
        aggregate_from_news(
            (a, b), asset_id="TSLA", window_start=0.0, window_end=300.0, source=_source()
        )


def test_aggregated_sentiment_rejects_window_end_before_start() -> None:
    with pytest.raises(AltDataInputError):
        AggregatedSentiment(
            asset_id="AAPL",
            window_start=100.0,
            window_end=50.0,
            release_date=110.0,
            mean_sentiment=Decimal("0"),
            volume=1,
            source=_source(),
        )


def test_aggregated_sentiment_reference_period_aliases_window_end() -> None:
    agg = AggregatedSentiment(
        asset_id="AAPL",
        window_start=0.0,
        window_end=100.0,
        release_date=110.0,
        mean_sentiment=Decimal("0.1"),
        volume=5,
        source=_source(),
    )
    assert agg.reference_period == 100.0


# --------------------------------------------------------------------------- #
# SatelliteObservation
# --------------------------------------------------------------------------- #


def test_satellite_observation_rejects_release_before_capture() -> None:
    with pytest.raises(AltDataInputError):
        SatelliteObservation(
            asset_id="XOM",
            metric_type=SatelliteMetricType.STORAGE_TANK_FILL_LEVEL,
            capture_date=100.0,
            release_date=50.0,
            value=Decimal("0.65"),
            source=_source(),
        )


def test_satellite_observation_reference_period_aliases_capture_date() -> None:
    obs = SatelliteObservation(
        asset_id="XOM",
        metric_type=SatelliteMetricType.STORAGE_TANK_FILL_LEVEL,
        capture_date=100.0,
        release_date=200.0,
        value=Decimal("0.65"),
        source=_source(),
    )
    assert obs.reference_period == 100.0


# --------------------------------------------------------------------------- #
# ShippingObservation
# --------------------------------------------------------------------------- #


def test_shipping_observation_rejects_negative_vessel_count() -> None:
    with pytest.raises(AltDataInputError):
        ShippingObservation(
            identifier="PORT_LA",
            metric_type=ShippingMetricType.VESSEL_COUNT,
            reference_period=0.0,
            release_date=10.0,
            value=Decimal("-5"),
            source=_source(),
        )


def test_shipping_observation_allows_negative_congestion_index() -> None:
    """PORT_CONGESTION_INDEX is not in the non-negative-constrained set -- an index
    could legitimately be expressed as a signed deviation from baseline."""
    obs = ShippingObservation(
        identifier="PORT_LA",
        metric_type=ShippingMetricType.PORT_CONGESTION_INDEX,
        reference_period=0.0,
        release_date=10.0,
        value=Decimal("-1.2"),
        source=_source(),
    )
    assert obs.value == Decimal("-1.2")


# --------------------------------------------------------------------------- #
# ESGScore / composite_score
# --------------------------------------------------------------------------- #


def _esg() -> ESGScore:
    return ESGScore(
        asset_id="AAPL",
        reference_period=0.0,
        release_date=10.0,
        environmental_score=Decimal("80"),
        social_score=Decimal("60"),
        governance_score=Decimal("70"),
        source=_source(),
    )


def test_esg_score_rejects_out_of_range_sub_score() -> None:
    with pytest.raises(AltDataInputError):
        ESGScore(
            asset_id="AAPL",
            reference_period=0.0,
            release_date=10.0,
            environmental_score=Decimal("150"),
            social_score=Decimal("60"),
            governance_score=Decimal("70"),
            source=_source(),
        )


def test_composite_score_equal_weight() -> None:
    result = composite_score(_esg())
    assert result == pytest.approx(Decimal("70"), abs=Decimal("0.01"))


def test_composite_score_custom_weights() -> None:
    """100% environmental weight -> composite equals the environmental score exactly."""
    result = composite_score(_esg(), weights=(Decimal("1"), Decimal("0"), Decimal("0")))
    assert result == Decimal("80")


def test_composite_score_rejects_weights_not_summing_to_one() -> None:
    with pytest.raises(AltDataInputError):
        composite_score(_esg(), weights=(Decimal("0.5"), Decimal("0.5"), Decimal("0.5")))


# --------------------------------------------------------------------------- #
# CreditCardSpendingObservation
# --------------------------------------------------------------------------- #


def test_credit_card_observation_rejects_release_before_reference() -> None:
    with pytest.raises(AltDataInputError):
        CreditCardSpendingObservation(
            asset_id="WMT",
            reference_period=100.0,
            release_date=50.0,
            spending_growth_yoy=Decimal("0.05"),
            source=_source(),
        )


def test_credit_card_observation_transaction_count_defaults_to_none() -> None:
    obs = CreditCardSpendingObservation(
        asset_id="WMT",
        reference_period=0.0,
        release_date=10.0,
        spending_growth_yoy=Decimal("0.05"),
        source=_source(),
    )
    assert obs.transaction_count_growth_yoy is None


# --------------------------------------------------------------------------- #
# Feature Store bridge: proves real integration, not just structural typing
# --------------------------------------------------------------------------- #


def test_from_news_sentiment_satisfies_feature_value_protocol() -> None:
    item = NewsSentiment(
        asset_id="AAPL",
        headline="H",
        published_at=100.0,
        release_date=110.0,
        sentiment_score=Decimal("0.5"),
        source=_source(),
    )
    converted = from_news_sentiment(item, feature_id="news_sentiment_1d", version=1)
    accepted: FeatureValueProtocol = converted
    assert accepted.feature_id == "news_sentiment_1d"
    assert accepted.value == 0.5
    assert accepted.timestamp == 110.0


def test_from_news_sentiment_flows_through_real_feature_store_adapter() -> None:
    item = NewsSentiment(
        asset_id="AAPL",
        headline="H",
        published_at=100.0,
        release_date=110.0,
        sentiment_score=Decimal("0.5"),
        source=_source(),
    )
    converted = from_news_sentiment(item, feature_id="news_sentiment_1d", version=1)
    result = FeatureValueAdapter.to_feature_value(converted)
    assert result.feature_id == "news_sentiment_1d"
    assert result.asset_id == "AAPL"
    assert result.value == 0.5


def test_from_aggregated_sentiment_bridge() -> None:
    a, b = _two_articles()
    agg = aggregate_from_news(
        (a, b), asset_id="AAPL", window_start=0.0, window_end=300.0, source=_source()
    )
    converted = from_aggregated_sentiment(agg, feature_id="agg_sentiment", version=1)
    assert converted.value == pytest.approx(0.15)
    assert converted.timestamp == 210.0


def test_from_satellite_observation_bridge() -> None:
    obs = SatelliteObservation(
        asset_id="XOM",
        metric_type=SatelliteMetricType.STORAGE_TANK_FILL_LEVEL,
        capture_date=100.0,
        release_date=200.0,
        value=Decimal("0.65"),
        source=_source(),
    )
    converted = from_satellite_observation(obs, feature_id="tank_fill", version=1)
    assert converted.value == 0.65
    assert converted.timestamp == 200.0


def test_from_shipping_observation_bridge_uses_identifier_as_asset_id() -> None:
    obs = ShippingObservation(
        identifier="PORT_LA",
        metric_type=ShippingMetricType.VESSEL_COUNT,
        reference_period=0.0,
        release_date=10.0,
        value=Decimal("42"),
        source=_source(),
    )
    converted = from_shipping_observation(obs, feature_id="port_vessel_count", version=1)
    assert converted.asset_id == "PORT_LA"
    assert converted.value == 42.0


def test_from_esg_score_bridge_defaults_to_equal_weight() -> None:
    converted = from_esg_score(_esg(), feature_id="esg_composite", version=1)
    assert converted.value == pytest.approx(70.0, abs=0.01)


def test_from_esg_score_bridge_with_custom_weights() -> None:
    converted = from_esg_score(
        _esg(),
        feature_id="esg_composite",
        version=1,
        weights=(Decimal("1"), Decimal("0"), Decimal("0")),
    )
    assert converted.value == 80.0


def test_from_credit_card_observation_bridge() -> None:
    obs = CreditCardSpendingObservation(
        asset_id="WMT",
        reference_period=0.0,
        release_date=10.0,
        spending_growth_yoy=Decimal("0.05"),
        source=_source(),
    )
    converted = from_credit_card_observation(obs, feature_id="cc_spend_growth", version=1)
    assert converted.value == pytest.approx(0.05)
    assert converted.asset_id == "WMT"
