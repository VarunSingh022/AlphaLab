"""High-level facade orchestrating the Feature Store lifecycle."""

from alphalab.feature_store.decision import FeatureWriteDecision
from alphalab.feature_store.metadata import FeatureMetadata
from alphalab.feature_store.registry import FeatureRegistry
from alphalab.feature_store.state import FeatureStoreState
from alphalab.feature_store.store import FeatureValueStore
from alphalab.feature_store.value import FeatureValue


class FeatureStoreEngine:
    """Facade orchestrating safe interaction with the Feature Store subsystems."""

    @staticmethod
    def initialize(engine_id: str) -> FeatureStoreState:
        """Constructs an empty base state for the Feature Store."""
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        return FeatureStoreState(engine_id=engine_id)

    @staticmethod
    def reset(engine_id: str) -> FeatureStoreState:
        """Returns a fresh Feature Store state, discarding all prior registrations."""
        return FeatureStoreEngine.initialize(engine_id)

    @staticmethod
    def register_feature(
        state: FeatureStoreState, metadata: FeatureMetadata, timestamp: float
    ) -> FeatureStoreState:
        """Registers a new feature definition version."""
        return FeatureRegistry.register(state, metadata, timestamp)

    @staticmethod
    def deprecate_feature(
        state: FeatureStoreState, feature_id: str, version: int, timestamp: float
    ) -> FeatureStoreState:
        """Marks a registered feature version as deprecated."""
        return FeatureRegistry.deprecate(state, feature_id, version, timestamp)

    @staticmethod
    def write_value(
        state: FeatureStoreState, value: FeatureValue, timestamp: float
    ) -> tuple[FeatureStoreState, FeatureWriteDecision]:
        """Validates and writes a computed feature value."""
        return FeatureValueStore.write(state, value, timestamp)
