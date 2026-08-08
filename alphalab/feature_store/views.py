"""Pure queries exposing transparent Feature Store state access."""

from collections.abc import Sequence

from alphalab.feature_store.cache import cache_key
from alphalab.feature_store.decision import FeatureWriteDecision
from alphalab.feature_store.metadata import FeatureMetadata
from alphalab.feature_store.state import FeatureStoreState, FeatureStoreStatistics
from alphalab.feature_store.value import FeatureValue


def get_metadata(state: FeatureStoreState, feature_id: str, version: int) -> FeatureMetadata | None:
    """Returns the registered definition for a feature_id/version, or None."""
    return state.features.get(f"{feature_id}:{version}")


def list_features(state: FeatureStoreState) -> Sequence[FeatureMetadata]:
    """Returns every registered feature definition, across all versions."""
    return tuple(state.features.values())


def list_versions(state: FeatureStoreState, feature_id: str) -> Sequence[int]:
    """Returns every registered version number for a feature_id, ascending."""
    versions = sorted(
        meta.version for meta in state.features.values() if meta.feature_id == feature_id
    )
    return tuple(versions)


def active_features(state: FeatureStoreState) -> Sequence[FeatureMetadata]:
    """Returns registered features excluding deprecated feature_id/version pairs."""
    return tuple(meta for key, meta in state.features.items() if key not in state.deprecated_keys)


def deprecated_features(state: FeatureStoreState) -> Sequence[FeatureMetadata]:
    """Returns feature definitions that have been marked deprecated."""
    return tuple(meta for key, meta in state.features.items() if key in state.deprecated_keys)


def latest_value(
    state: FeatureStoreState, feature_id: str, version: int, asset_id: str | None
) -> FeatureValue | None:
    """Returns the most recently written value for a feature_id/version/asset_id."""
    return state.values.get(cache_key(feature_id, version, asset_id))


def write_history(state: FeatureStoreState) -> Sequence[FeatureWriteDecision]:
    """Returns every write decision ever produced, in chronological order."""
    return state.history


def feature_statistics(state: FeatureStoreState) -> FeatureStoreStatistics:
    """Returns aggregate registration and write counters."""
    return state.statistics
