"""Structural validation guards for Feature Store registration and lookup.

These raise immediately on structural problems -- empty identifiers, duplicate
registrations, unknown lookups. Semantic checks on written values (type mismatches,
scope mismatches) are pure functions in `feature_store.checks` instead, since those
accumulate into a `FeatureWriteDecision` rather than aborting the caller.
"""

from alphalab.feature_store.exceptions import (
    FeatureNotFoundError,
    FeatureValidationError,
    InvalidFeatureStateError,
)
from alphalab.feature_store.metadata import FeatureMetadata
from alphalab.feature_store.state import FeatureStoreState


def validate_feature_metadata(metadata: FeatureMetadata) -> None:
    """Ensures a feature definition conforms to standard identifying rules."""
    if not metadata.feature_id.strip():
        raise FeatureValidationError("feature_id cannot be empty.")
    if not metadata.name.strip():
        raise FeatureValidationError("name cannot be empty.")
    if not metadata.owner.strip():
        raise FeatureValidationError("owner cannot be empty.")
    if not metadata.description.strip():
        raise FeatureValidationError("description cannot be empty.")
    if metadata.version < 1:
        raise FeatureValidationError(f"version must be >= 1, got {metadata.version}.")


def validate_registration(state: FeatureStoreState, metadata: FeatureMetadata) -> None:
    """Checks for identity collisions before allowing a feature to register."""
    validate_feature_metadata(metadata)

    key = f"{metadata.feature_id}:{metadata.version}"
    if key in state.features:
        raise InvalidFeatureStateError(
            f"Feature '{metadata.feature_id}' version {metadata.version} is already registered."
        )


def validate_lookup(state: FeatureStoreState, feature_id: str, version: int) -> None:
    """Verifies a feature version exists in the registry."""
    key = f"{feature_id}:{version}"
    if key not in state.features:
        raise FeatureNotFoundError(f"Feature '{feature_id}' version {version} not found.")
