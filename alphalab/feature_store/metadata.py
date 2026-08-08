"""Immutable definitions for feature classification and metadata.

Feature Store owns registration, versioning, metadata, validation, and caching of
feature definitions and values. It does not compute features -- computation is the
responsibility of consumers implementing `feature_store.protocol.FeatureComputeProtocol`
(e.g. a future Factor Library), which write results back through this store. This
mirrors the existing broker/OMS decoupling in `alphalab.broker.adapter.OMSOrderProtocol`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto


class FeatureType(Enum):
    """Classifies the data lineage a feature is derived from."""

    PRICE = auto()
    VOLUME = auto()
    FUNDAMENTAL = auto()
    ALTERNATIVE = auto()
    CROSS_SECTIONAL = auto()
    DERIVED = auto()


class FeatureValueType(Enum):
    """Runtime data type a feature's stored values must conform to."""

    FLOAT = auto()
    DECIMAL = auto()
    INTEGER = auto()
    BOOLEAN = auto()
    STRING = auto()


@dataclass(frozen=True, slots=True)
class FeatureMetadata:
    """Immutable record describing a registered feature definition.

    Attributes:
        feature_id: Stable identifier for the feature (e.g. "momentum_20d").
        name: Human-readable display name.
        version: Monotonically increasing definition version for this feature_id.
            Registering a new version does not overwrite prior versions -- both
            remain queryable, consistent with the "Versioning" requirement.
        feature_type: Data lineage classification.
        value_type: Runtime type stored values must conform to.
        owner: Person or system responsible for the feature definition.
        description: Human-readable explanation of what the feature represents.
        asset_scoped: True if values are keyed per-asset; False for cross-sectional
            or market-wide features with no single asset identifier.
        depends_on: feature_ids this feature's computation depends on, for lineage
            tracking. Does not imply Feature Store computes anything itself.
        tags: Free-form key/value labels for discovery and filtering.
        created_at: Unix timestamp of registration.
    """

    feature_id: str
    name: str
    version: int
    feature_type: FeatureType
    value_type: FeatureValueType
    owner: str
    description: str
    asset_scoped: bool = True
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    tags: Mapping[str, str] = field(default_factory=dict)
    created_at: float = 0.0
