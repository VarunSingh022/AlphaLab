"""Comprehensive tests for the Feature Store: registry, values, cache, and views."""

from dataclasses import FrozenInstanceError

import pytest

from alphalab.feature_store import (
    FeatureCache,
    FeatureMetadata,
    FeatureNotFoundError,
    FeatureRegistry,
    FeatureStoreEngine,
    FeatureStoreError,
    FeatureStoreState,
    FeatureType,
    FeatureValidationError,
    FeatureValue,
    FeatureValueAdapter,
    FeatureValueProtocol,
    FeatureValueStore,
    FeatureValueType,
    FeatureViolation,
    InvalidFeatureStateError,
    active_features,
    cache_key,
    check_asset_scope,
    check_dependencies_registered,
    check_feature_registered,
    check_value_type,
    deprecated_features,
    feature_statistics,
    get_metadata,
    latest_value,
    list_features,
    list_versions,
    validate_feature_metadata,
    validate_lookup,
    validate_registration,
    write_history,
)
from alphalab.feature_store.cache import cache_value, cached_value, clear, invalidate


def _momentum_metadata(version: int = 1, depends_on: tuple[str, ...] = ()) -> FeatureMetadata:
    return FeatureMetadata(
        feature_id="momentum_20d",
        name="20-Day Momentum",
        version=version,
        feature_type=FeatureType.PRICE,
        value_type=FeatureValueType.FLOAT,
        owner="quant-research",
        description="20-day trailing price momentum.",
        depends_on=depends_on,
        created_at=1000.0,
    )


def _momentum_value(
    version: int = 1, asset_id: str | None = "AAPL", value: float = 0.045
) -> FeatureValue:
    return FeatureValue(
        feature_id="momentum_20d", version=version, asset_id=asset_id, value=value, timestamp=1000.0
    )


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #


def test_feature_metadata_defaults() -> None:
    meta = _momentum_metadata()
    assert meta.asset_scoped is True
    assert meta.depends_on == ()
    assert meta.tags == {}


def test_feature_metadata_is_immutable() -> None:
    meta = _momentum_metadata()
    with pytest.raises(FrozenInstanceError):
        meta.name = "renamed"  # type: ignore[misc]


def test_feature_metadata_versioning_produces_distinct_instances() -> None:
    v1 = _momentum_metadata(version=1)
    v2 = _momentum_metadata(version=2)
    assert v1 != v2
    assert v1.feature_id == v2.feature_id
    assert v1.version != v2.version


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


def test_exception_hierarchy() -> None:
    assert issubclass(FeatureValidationError, FeatureStoreError)
    assert issubclass(InvalidFeatureStateError, FeatureStoreError)
    assert issubclass(FeatureNotFoundError, FeatureStoreError)


# --------------------------------------------------------------------------- #
# Protocol / Adapter decoupling
# --------------------------------------------------------------------------- #


class _ComputedMomentumValue:
    """Structural stand-in for a Factor-Library-produced value."""

    def __init__(self, feature_id: str, version: int, asset_id: str, value: float) -> None:
        self._feature_id = feature_id
        self._version = version
        self._asset_id = asset_id
        self._value = value

    @property
    def feature_id(self) -> str:
        return self._feature_id

    @property
    def version(self) -> int:
        return self._version

    @property
    def asset_id(self) -> str | None:
        return self._asset_id

    @property
    def value(self) -> float:
        return self._value

    @property
    def timestamp(self) -> float:
        return 1000.0


def test_external_value_satisfies_protocol_structurally() -> None:
    computed = _ComputedMomentumValue("momentum_20d", 1, "AAPL", 0.045)
    accepted: FeatureValueProtocol = computed
    assert accepted.feature_id == "momentum_20d"


def test_adapter_converts_protocol_value_to_feature_value() -> None:
    computed = _ComputedMomentumValue("momentum_20d", 1, "AAPL", 0.045)
    converted = FeatureValueAdapter.to_feature_value(computed)
    assert isinstance(converted, FeatureValue)
    assert converted.feature_id == "momentum_20d"
    assert converted.asset_id == "AAPL"
    assert converted.value == 0.045


# --------------------------------------------------------------------------- #
# Validation guards (structural, raise-based)
# --------------------------------------------------------------------------- #


def test_validate_feature_metadata_rejects_empty_feature_id() -> None:
    meta = FeatureMetadata(
        feature_id="",
        name="X",
        version=1,
        feature_type=FeatureType.PRICE,
        value_type=FeatureValueType.FLOAT,
        owner="quant-research",
        description="x",
    )
    with pytest.raises(FeatureValidationError):
        validate_feature_metadata(meta)


def test_validate_feature_metadata_rejects_zero_version() -> None:
    meta = FeatureMetadata(
        feature_id="x",
        name="X",
        version=0,
        feature_type=FeatureType.PRICE,
        value_type=FeatureValueType.FLOAT,
        owner="quant-research",
        description="x",
    )
    with pytest.raises(FeatureValidationError):
        validate_feature_metadata(meta)


def test_validate_registration_rejects_duplicate() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    with pytest.raises(InvalidFeatureStateError):
        validate_registration(state, _momentum_metadata())


def test_validate_lookup_raises_when_missing() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    with pytest.raises(FeatureNotFoundError):
        validate_lookup(state, "unknown", 1)


# --------------------------------------------------------------------------- #
# Registry: registration approval / rejection
# --------------------------------------------------------------------------- #


def test_register_feature_succeeds() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    new_state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)

    assert get_metadata(new_state, "momentum_20d", 1) == _momentum_metadata()
    assert new_state.statistics.total_registered == 1
    assert len(new_state.events) == 1


def test_register_feature_rejects_duplicate_version() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    with pytest.raises(InvalidFeatureStateError):
        FeatureRegistry.register(state, _momentum_metadata(), 1001.0)


def test_register_new_version_of_existing_feature_succeeds() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(version=1), 1000.0)
    state = FeatureRegistry.register(state, _momentum_metadata(version=2), 1001.0)

    assert list_versions(state, "momentum_20d") == (1, 2)
    assert state.statistics.total_registered == 2


def test_register_rejects_missing_dependency() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    dependent = _momentum_metadata(depends_on=("nonexistent_feature",))
    with pytest.raises(FeatureValidationError):
        FeatureRegistry.register(state, dependent, 1000.0)


def test_register_succeeds_when_dependency_already_registered() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)

    derived = FeatureMetadata(
        feature_id="sector_relative_momentum",
        name="Sector-Relative Momentum",
        version=1,
        feature_type=FeatureType.CROSS_SECTIONAL,
        value_type=FeatureValueType.FLOAT,
        owner="quant-research",
        description="Momentum relative to sector peers.",
        asset_scoped=False,
        depends_on=("momentum_20d",),
    )
    new_state = FeatureRegistry.register(state, derived, 1001.0)
    assert get_metadata(new_state, "sector_relative_momentum", 1) is not None


def test_deprecate_feature_succeeds() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    state = FeatureRegistry.deprecate(state, "momentum_20d", 1, 1001.0)

    assert state.statistics.total_deprecated == 1
    assert "momentum_20d:1" in state.deprecated_keys
    # Still queryable, just excluded from active_features.
    assert get_metadata(state, "momentum_20d", 1) is not None
    assert get_metadata(state, "momentum_20d", 1) not in active_features(state)
    assert get_metadata(state, "momentum_20d", 1) in deprecated_features(state)


def test_deprecate_unregistered_feature_raises() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    with pytest.raises(FeatureNotFoundError):
        FeatureRegistry.deprecate(state, "unknown", 1, 1000.0)


def test_deprecate_already_deprecated_feature_raises() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    state = FeatureRegistry.deprecate(state, "momentum_20d", 1, 1001.0)
    with pytest.raises(InvalidFeatureStateError):
        FeatureRegistry.deprecate(state, "momentum_20d", 1, 1002.0)


# --------------------------------------------------------------------------- #
# Checks (pure, return Violation | None)
# --------------------------------------------------------------------------- #


def test_check_feature_registered_passes_when_registered() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    assert check_feature_registered(_momentum_value(), state) is None


def test_check_feature_registered_fails_when_unregistered() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    result = check_feature_registered(_momentum_value(), state)
    assert isinstance(result, FeatureViolation)
    assert result.rule == "FeatureRegistered"


def test_check_value_type_passes_for_correct_type() -> None:
    meta = _momentum_metadata()
    assert check_value_type(_momentum_value(value=0.05), meta) is None


def test_check_value_type_fails_for_wrong_type() -> None:
    meta = _momentum_metadata()
    bad_value = FeatureValue(
        feature_id="momentum_20d", version=1, asset_id="AAPL", value="not-a-float", timestamp=1000.0
    )
    result = check_value_type(bad_value, meta)
    assert isinstance(result, FeatureViolation)
    assert result.rule == "ValueType"


def test_check_value_type_rejects_bool_for_integer_feature() -> None:
    """bool is a subclass of int in Python; this proves the check isn't fooled by that."""
    meta = FeatureMetadata(
        feature_id="trade_count",
        name="Trade Count",
        version=1,
        feature_type=FeatureType.VOLUME,
        value_type=FeatureValueType.INTEGER,
        owner="quant-research",
        description="Daily trade count.",
    )
    bool_value = FeatureValue(
        feature_id="trade_count", version=1, asset_id="AAPL", value=True, timestamp=1000.0
    )
    result = check_value_type(bool_value, meta)
    assert isinstance(result, FeatureViolation)


def test_check_asset_scope_fails_when_scoped_feature_missing_asset_id() -> None:
    meta = _momentum_metadata()
    value = _momentum_value(asset_id=None)
    result = check_asset_scope(value, meta)
    assert isinstance(result, FeatureViolation)
    assert result.rule == "AssetScope"


def test_check_asset_scope_fails_when_unscoped_feature_has_asset_id() -> None:
    meta = FeatureMetadata(
        feature_id="market_regime",
        name="Market Regime",
        version=1,
        feature_type=FeatureType.CROSS_SECTIONAL,
        value_type=FeatureValueType.STRING,
        owner="quant-research",
        description="Overall market regime classification.",
        asset_scoped=False,
    )
    value = FeatureValue(
        feature_id="market_regime", version=1, asset_id="AAPL", value="risk_on", timestamp=1000.0
    )
    result = check_asset_scope(value, meta)
    assert isinstance(result, FeatureViolation)


def test_check_dependencies_registered_fails_for_missing_dependency() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    meta = _momentum_metadata(depends_on=("missing",))
    result = check_dependencies_registered(meta, state)
    assert isinstance(result, FeatureViolation)
    assert "missing" in result.current_value


# --------------------------------------------------------------------------- #
# Value writes: approval / rejection via FeatureValueStore
# --------------------------------------------------------------------------- #


def test_write_value_approved() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)

    new_state, decision = FeatureValueStore.write(state, _momentum_value(), 1001.0)

    assert decision.approved is True
    assert decision.violations == ()
    assert new_state.statistics.total_values_written == 1
    assert latest_value(new_state, "momentum_20d", 1, "AAPL") == _momentum_value()


def test_write_value_rejected_when_feature_unregistered() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    new_state, decision = FeatureValueStore.write(state, _momentum_value(), 1000.0)

    assert decision.approved is False
    assert len(decision.violations) == 1
    assert decision.violations[0].rule == "FeatureRegistered"
    assert new_state.statistics.total_values_rejected == 1
    # Rejected write must not appear in values or cache.
    assert latest_value(new_state, "momentum_20d", 1, "AAPL") is None


def test_write_value_rejected_for_wrong_type_does_not_mutate_values() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    bad_value = FeatureValue(
        feature_id="momentum_20d", version=1, asset_id="AAPL", value="oops", timestamp=1001.0
    )

    new_state, decision = FeatureValueStore.write(state, bad_value, 1001.0)

    assert decision.approved is False
    assert latest_value(new_state, "momentum_20d", 1, "AAPL") is None


def test_write_value_accumulates_multiple_violations() -> None:
    """A value that is both wrong-typed AND wrong-scoped reports both, not just one."""
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    bad_value = FeatureValue(
        feature_id="momentum_20d", version=1, asset_id=None, value="oops", timestamp=1001.0
    )

    _, decision = FeatureValueStore.write(state, bad_value, 1001.0)

    assert decision.approved is False
    assert len(decision.violations) == 2
    rules = {v.rule for v in decision.violations}
    assert rules == {"ValueType", "AssetScope"}


def test_write_updates_cache() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    new_state, _ = FeatureValueStore.write(state, _momentum_value(), 1001.0)

    key = cache_key("momentum_20d", 1, "AAPL")
    assert key in new_state.cache.entries
    assert new_state.cache.entries[key] == _momentum_value()


# --------------------------------------------------------------------------- #
# Cache: pure functional hit / miss / invalidate
# --------------------------------------------------------------------------- #


def test_cache_value_is_pure_and_returns_new_instance() -> None:
    original = FeatureCache()
    updated = cache_value(original, _momentum_value())
    assert original.entries == {}
    assert updated.entries != {}
    assert original is not updated


def test_cached_value_hit_increments_hits() -> None:
    cache = cache_value(FeatureCache(), _momentum_value())
    new_cache, found = cached_value(cache, "momentum_20d", 1, "AAPL")
    assert found == _momentum_value()
    assert new_cache.hits == 1
    assert new_cache.misses == 0


def test_cached_value_miss_increments_misses() -> None:
    cache = FeatureCache()
    new_cache, found = cached_value(cache, "momentum_20d", 1, "AAPL")
    assert found is None
    assert new_cache.misses == 1
    assert new_cache.hits == 0


def test_invalidate_removes_entry() -> None:
    cache = cache_value(FeatureCache(), _momentum_value())
    invalidated = invalidate(cache, "momentum_20d", 1, "AAPL")
    _, found = cached_value(invalidated, "momentum_20d", 1, "AAPL")
    assert found is None


def test_invalidate_missing_key_is_a_no_op() -> None:
    cache = FeatureCache()
    result = invalidate(cache, "momentum_20d", 1, "AAPL")
    assert result is cache


def test_clear_resets_cache_fully() -> None:
    cache = cache_value(FeatureCache(), _momentum_value())
    cleared = clear(cache)
    assert cleared.entries == {}
    assert cleared.hits == 0
    assert cleared.misses == 0


# --------------------------------------------------------------------------- #
# Immutability & determinism
# --------------------------------------------------------------------------- #


def test_feature_store_state_is_immutable() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    with pytest.raises(FrozenInstanceError):
        state.engine_id = "FS-2"  # type: ignore[misc]


def test_write_does_not_mutate_input_state() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    snapshot_values = dict(state.values)

    FeatureValueStore.write(state, _momentum_value(), 1001.0)

    assert state.values == snapshot_values


def test_evaluation_is_deterministic() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)

    _, decision_a = FeatureValueStore.write(state, _momentum_value(), 1001.0)
    _, decision_b = FeatureValueStore.write(state, _momentum_value(), 1001.0)

    assert decision_a.approved == decision_b.approved
    assert decision_a.reason == decision_b.reason
    assert decision_a.violations == decision_b.violations


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #


def test_list_features_returns_all_registered() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(version=1), 1000.0)
    state = FeatureRegistry.register(state, _momentum_metadata(version=2), 1001.0)
    assert len(list_features(state)) == 2


def test_feature_statistics_tracks_registrations_and_writes() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    state, _ = FeatureValueStore.write(state, _momentum_value(), 1001.0)

    stats = feature_statistics(state)
    assert stats.total_registered == 1
    assert stats.total_values_written == 1
    assert stats.total_values_rejected == 0


def test_write_history_records_every_decision_in_order() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureRegistry.register(state, _momentum_metadata(), 1000.0)
    state, decision_1 = FeatureValueStore.write(state, _momentum_value(value=0.01), 1001.0)
    state, decision_2 = FeatureValueStore.write(state, _momentum_value(value=0.02), 1002.0)

    history = write_history(state)
    assert history == (decision_1, decision_2)


# --------------------------------------------------------------------------- #
# Engine facade: end-to-end
# --------------------------------------------------------------------------- #


def test_engine_initialize_rejects_empty_id() -> None:
    with pytest.raises(ValueError):
        FeatureStoreEngine.initialize("")


def test_engine_end_to_end_register_write_read() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureStoreEngine.register_feature(state, _momentum_metadata(), 1000.0)
    state, decision = FeatureStoreEngine.write_value(state, _momentum_value(), 1001.0)

    assert decision.approved is True
    assert latest_value(state, "momentum_20d", 1, "AAPL") == _momentum_value()


def test_engine_reset_discards_all_state() -> None:
    state = FeatureStoreEngine.initialize("FS-1")
    state = FeatureStoreEngine.register_feature(state, _momentum_metadata(), 1000.0)

    reset_state = FeatureStoreEngine.reset("FS-1")
    assert reset_state.features == {}
    assert isinstance(reset_state, FeatureStoreState)
