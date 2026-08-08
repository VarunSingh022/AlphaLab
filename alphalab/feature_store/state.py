"""Global immutable state container for the Feature Store."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.feature_store.cache import FeatureCache
from alphalab.feature_store.decision import FeatureWriteDecision
from alphalab.feature_store.events import FeatureStoreEvent
from alphalab.feature_store.metadata import FeatureMetadata
from alphalab.feature_store.value import FeatureValue


@dataclass(frozen=True, slots=True)
class FeatureStoreStatistics:
    """Immutable tracking metrics for the Feature Store."""

    total_registered: int = 0
    total_deprecated: int = 0
    total_values_written: int = 0
    total_values_rejected: int = 0


@dataclass(frozen=True, slots=True)
class FeatureStoreState:
    """Deterministic snapshot of registered features, values, and cache contents.

    Attributes:
        engine_id: Identifier for this Feature Store instance.
        features: Registered feature definitions, keyed by "feature_id:version".
        deprecated_keys: "feature_id:version" keys marked deprecated. Deprecated
            features remain in `features` and queryable, they are simply excluded
            from `views.active_features`.
        values: Most recently written value per "feature_id:version:asset_id" key.
        cache: Read-through cache of the same values, tracked separately so cache
            hit/miss statistics reflect read access patterns independent of writes.
        history: Every write decision ever produced, in order.
        events: Every domain event ever emitted, in order.
        statistics: Aggregate counters for registrations, deprecations, and writes.
        metadata: Free-form key/value labels for this Feature Store instance.
    """

    engine_id: str
    features: Mapping[str, FeatureMetadata] = field(default_factory=dict)
    deprecated_keys: frozenset[str] = field(default_factory=frozenset)
    values: Mapping[str, FeatureValue] = field(default_factory=dict)
    cache: FeatureCache = field(default_factory=FeatureCache)
    history: tuple[FeatureWriteDecision, ...] = field(default_factory=tuple)
    events: tuple[FeatureStoreEvent, ...] = field(default_factory=tuple)
    statistics: FeatureStoreStatistics = field(default_factory=FeatureStoreStatistics)
    metadata: Mapping[str, str] = field(default_factory=dict)
