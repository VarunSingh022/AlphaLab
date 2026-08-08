"""Adapter translating externally computed values into Feature Store structures."""

from alphalab.feature_store.protocol import FeatureValueProtocol
from alphalab.feature_store.value import FeatureValue


class FeatureValueAdapter:
    """Stateless translator mapping computed values into the Feature Store domain.

    Accepts anything structurally satisfying `FeatureValueProtocol` -- a future
    Factor Library never needs to be imported by, or aware of, this package.
    """

    @staticmethod
    def to_feature_value(computed: FeatureValueProtocol) -> FeatureValue:
        """Converts a protocol-conforming computed value into an immutable FeatureValue."""
        return FeatureValue(
            feature_id=computed.feature_id,
            version=computed.version,
            asset_id=computed.asset_id,
            value=computed.value,
            timestamp=computed.timestamp,
        )
