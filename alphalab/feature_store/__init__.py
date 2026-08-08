"""AlphaLab Feature Store.

Registration, versioning, metadata, validation, and caching for feature definitions
and values. Feature Store does not compute features -- see
`alphalab.feature_store.protocol.FeatureValueProtocol` for the seam a computation
engine (e.g. a future Factor Library) writes through, and
`alphalab.feature_store.adapter.FeatureValueAdapter` for the conversion boundary.
"""

from alphalab.feature_store.adapter import FeatureValueAdapter
from alphalab.feature_store.cache import FeatureCache, cache_key
from alphalab.feature_store.checks import (
    check_asset_scope,
    check_dependencies_registered,
    check_feature_registered,
    check_value_type,
)
from alphalab.feature_store.decision import FeatureWriteDecision
from alphalab.feature_store.engine import FeatureStoreEngine
from alphalab.feature_store.events import (
    CacheHit,
    CacheInvalidated,
    CacheMiss,
    FeatureDeprecated,
    FeatureRegistered,
    FeatureStoreEvent,
    FeatureValueRejected,
    FeatureValueWritten,
)
from alphalab.feature_store.exceptions import (
    FeatureNotFoundError,
    FeatureStoreError,
    FeatureValidationError,
    InvalidFeatureStateError,
)
from alphalab.feature_store.metadata import FeatureMetadata, FeatureType, FeatureValueType
from alphalab.feature_store.protocol import FeatureValueProtocol
from alphalab.feature_store.registry import FeatureRegistry
from alphalab.feature_store.state import FeatureStoreState, FeatureStoreStatistics
from alphalab.feature_store.store import FeatureValueStore
from alphalab.feature_store.validation import (
    validate_feature_metadata,
    validate_lookup,
    validate_registration,
)
from alphalab.feature_store.value import FeatureValue
from alphalab.feature_store.views import (
    active_features,
    deprecated_features,
    feature_statistics,
    get_metadata,
    latest_value,
    list_features,
    list_versions,
    write_history,
)
from alphalab.feature_store.violations import FeatureViolation

__all__ = [
    "CacheHit",
    "CacheInvalidated",
    "CacheMiss",
    "FeatureCache",
    "FeatureDeprecated",
    "FeatureMetadata",
    "FeatureNotFoundError",
    "FeatureRegistered",
    "FeatureRegistry",
    "FeatureStoreEngine",
    "FeatureStoreError",
    "FeatureStoreEvent",
    "FeatureStoreState",
    "FeatureStoreStatistics",
    "FeatureType",
    "FeatureValidationError",
    "FeatureValue",
    "FeatureValueAdapter",
    "FeatureValueProtocol",
    "FeatureValueRejected",
    "FeatureValueStore",
    "FeatureValueType",
    "FeatureValueWritten",
    "FeatureViolation",
    "FeatureWriteDecision",
    "InvalidFeatureStateError",
    "active_features",
    "cache_key",
    "check_asset_scope",
    "check_dependencies_registered",
    "check_feature_registered",
    "check_value_type",
    "deprecated_features",
    "feature_statistics",
    "get_metadata",
    "latest_value",
    "list_features",
    "list_versions",
    "validate_feature_metadata",
    "validate_lookup",
    "validate_registration",
    "write_history",
]
