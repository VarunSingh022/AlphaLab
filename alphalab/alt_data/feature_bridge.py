"""Bridge from alt-data observations into Feature Store.

Mirrors `alphalab.factor_library.result.FactorResult`: a small type that
structurally satisfies `alphalab.feature_store.protocol.FeatureValueProtocol`, so
any alt-data observation can be written into Feature Store through
`alphalab.feature_store.adapter.FeatureValueAdapter` without Feature Store ever
importing this package.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.alt_data.credit_card import CreditCardSpendingObservation
from alphalab.alt_data.esg import ESGScore, composite_score
from alphalab.alt_data.news import NewsSentiment
from alphalab.alt_data.satellite import SatelliteObservation
from alphalab.alt_data.sentiment import AggregatedSentiment
from alphalab.alt_data.shipping import ShippingObservation


@dataclass(frozen=True, slots=True)
class AltDataFeatureValue:
    """A single alt-data-derived value, shaped for Feature Store ingestion.

    Attributes:
        feature_id: Identifier matching a Feature Store registration. Alt-data
            observations don't register features themselves -- see
            alphalab.feature_store.registry.FeatureRegistry.
        version: Feature Store definition version this value was computed for.
        asset_id: Asset the value applies to.
        value: The feature value.
        timestamp: Unix timestamp this value became known -- always the
            observation's release_date, not any earlier reference/capture date, to
            preserve point-in-time correctness through the bridge.
    """

    feature_id: str
    version: int
    asset_id: str | None
    value: float
    timestamp: float


def from_news_sentiment(obs: NewsSentiment, feature_id: str, version: int) -> AltDataFeatureValue:
    """Converts a single article's sentiment score into a feature value."""
    return AltDataFeatureValue(
        feature_id=feature_id,
        version=version,
        asset_id=obs.asset_id,
        value=float(obs.sentiment_score),
        timestamp=obs.release_date,
    )


def from_aggregated_sentiment(
    obs: AggregatedSentiment, feature_id: str, version: int
) -> AltDataFeatureValue:
    """Converts a windowed sentiment aggregate into a feature value."""
    return AltDataFeatureValue(
        feature_id=feature_id,
        version=version,
        asset_id=obs.asset_id,
        value=float(obs.mean_sentiment),
        timestamp=obs.release_date,
    )


def from_satellite_observation(
    obs: SatelliteObservation, feature_id: str, version: int
) -> AltDataFeatureValue:
    """Converts a satellite-derived metric into a feature value."""
    return AltDataFeatureValue(
        feature_id=feature_id,
        version=version,
        asset_id=obs.asset_id,
        value=float(obs.value),
        timestamp=obs.release_date,
    )


def from_shipping_observation(
    obs: ShippingObservation, feature_id: str, version: int
) -> AltDataFeatureValue:
    """Converts a shipping-derived metric into a feature value."""
    return AltDataFeatureValue(
        feature_id=feature_id,
        version=version,
        asset_id=obs.identifier,
        value=float(obs.value),
        timestamp=obs.release_date,
    )


def from_esg_score(
    obs: ESGScore,
    feature_id: str,
    version: int,
    weights: tuple[Decimal, Decimal, Decimal] | None = None,
) -> AltDataFeatureValue:
    """Converts an ESG score's composite into a feature value.

    Args:
        obs: The score to convert.
        feature_id: Target Feature Store feature_id.
        version: Target Feature Store version.
        weights: Optional (environmental, social, governance) weights; defaults to
            `composite_score`'s equal weighting if omitted.
    """
    composite = composite_score(obs) if weights is None else composite_score(obs, weights)

    return AltDataFeatureValue(
        feature_id=feature_id,
        version=version,
        asset_id=obs.asset_id,
        value=float(composite),
        timestamp=obs.release_date,
    )


def from_credit_card_observation(
    obs: CreditCardSpendingObservation, feature_id: str, version: int
) -> AltDataFeatureValue:
    """Converts a spending panel observation into a feature value."""
    return AltDataFeatureValue(
        feature_id=feature_id,
        version=version,
        asset_id=obs.asset_id,
        value=float(obs.spending_growth_yoy),
        timestamp=obs.release_date,
    )
