"""Pure functional mutators managing feature registration and versioning."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.feature_store.checks import check_dependencies_registered
from alphalab.feature_store.events import FeatureDeprecated, FeatureRegistered
from alphalab.feature_store.exceptions import FeatureValidationError, InvalidFeatureStateError
from alphalab.feature_store.metadata import FeatureMetadata
from alphalab.feature_store.state import FeatureStoreState
from alphalab.feature_store.validation import validate_lookup, validate_registration


class FeatureRegistry:
    """Stateless dictionary transformations for the feature definition lifecycle."""

    @staticmethod
    def register(
        state: FeatureStoreState, metadata: FeatureMetadata, timestamp: float
    ) -> FeatureStoreState:
        """Registers a new feature definition version, returning updated state.

        Raises:
            FeatureValidationError: If metadata is structurally invalid, or a
                declared dependency is not yet registered.
            InvalidFeatureStateError: If this feature_id/version is already
                registered.
        """
        validate_registration(state, metadata)

        dependency_violation = check_dependencies_registered(metadata, state)
        if dependency_violation is not None:
            raise FeatureValidationError(dependency_violation.description)

        key = f"{metadata.feature_id}:{metadata.version}"
        new_features = dict(state.features)
        new_features[key] = metadata

        event = FeatureRegistered(str(new_id()), timestamp, metadata.feature_id, metadata.version)
        stats = replace(state.statistics, total_registered=state.statistics.total_registered + 1)

        return replace(
            state,
            features=new_features,
            events=(*state.events, event),
            statistics=stats,
        )

    @staticmethod
    def deprecate(
        state: FeatureStoreState, feature_id: str, version: int, timestamp: float
    ) -> FeatureStoreState:
        """Marks a registered feature version as deprecated, returning updated state.

        Deprecation does not remove the definition or its values -- both remain
        queryable, consistent with the store never destroying registered history.

        Raises:
            FeatureNotFoundError: If the feature_id/version is not registered.
            InvalidFeatureStateError: If the feature_id/version is already
                deprecated.
        """
        validate_lookup(state, feature_id, version)

        key = f"{feature_id}:{version}"
        if key in state.deprecated_keys:
            raise InvalidFeatureStateError(
                f"Feature '{feature_id}' version {version} is already deprecated."
            )

        new_deprecated = frozenset({*state.deprecated_keys, key})
        event = FeatureDeprecated(str(new_id()), timestamp, feature_id, version)
        stats = replace(state.statistics, total_deprecated=state.statistics.total_deprecated + 1)

        return replace(
            state,
            deprecated_keys=new_deprecated,
            events=(*state.events, event),
            statistics=stats,
        )
