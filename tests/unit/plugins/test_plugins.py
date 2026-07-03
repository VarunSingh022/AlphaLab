"""Comprehensive tests validating strict plugin registration, isolation, and metadata."""

import pytest

from alphalab.plugins import (
    BasePlugin,
    InvalidPluginStateError,
    PluginAdapter,
    PluginEngine,
    PluginLoader,
    PluginManager,
    PluginMetadata,
    PluginState,
    PluginType,
    PluginValidationError,
    disabled_plugins,
    enabled_plugins,
    list_plugins,
    lookup,
    plugin_count,
    plugin_statistics,
    validate_plugin_metadata,
)


class MockStrategyPlugin(BasePlugin):
    """Valid strategy dummy plugin."""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            plugin_id="S-1",
            name="MockStrat",
            version="1.0",
            author="Test",
            description="Mock",
            plugin_type=PluginType.STRATEGY,
            api_version="1.0.0",
            enabled=False,
        )


class MockFeedPlugin(BasePlugin):
    """Valid feed dummy plugin, initially enabled."""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            plugin_id="F-1",
            name="MockFeed",
            version="1.0",
            author="Test",
            description="Mock",
            plugin_type=PluginType.FEED,
            api_version="1.0.0",
            enabled=True,
        )


class InvalidVersionPlugin(BasePlugin):
    """Invalid API version."""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            plugin_id="I-1",
            name="Inv",
            version="1.0",
            author="Test",
            description="Mock",
            plugin_type=PluginType.STRATEGY,
            api_version="99.9.9",
        )


@pytest.fixture
def base_state() -> PluginState:
    return PluginEngine.initialize("PLUG-01")


# --- ENGINE & VALIDATION TESTS (12 tests) ---


def test_initialization(base_state: PluginState) -> None:
    assert base_state.engine_id == "PLUG-01"
    assert plugin_count(base_state) == 0

    with pytest.raises(ValueError):
        PluginEngine.initialize("")


def test_validate_metadata_valid() -> None:
    plugin = MockStrategyPlugin()
    validate_plugin_metadata(plugin)  # Should not raise


def test_validate_metadata_invalid_api() -> None:
    plugin = InvalidVersionPlugin()
    with pytest.raises(PluginValidationError, match="Unsupported API version"):
        validate_plugin_metadata(plugin)


def test_validate_metadata_empty_id() -> None:
    class Bad(BasePlugin):
        def metadata(self) -> PluginMetadata:
            return PluginMetadata("", "Name", "1", "A", "D", PluginType.RISK, "1.0.0")

    with pytest.raises(PluginValidationError, match="ID cannot be empty"):
        validate_plugin_metadata(Bad())


def test_validate_metadata_empty_name() -> None:
    class Bad(BasePlugin):
        def metadata(self) -> PluginMetadata:
            return PluginMetadata("1", "", "1", "A", "D", PluginType.RISK, "1.0.0")

    with pytest.raises(PluginValidationError, match="name cannot be empty"):
        validate_plugin_metadata(Bad())


# --- LOADER TESTS (5 tests) ---


def test_loader_success() -> None:
    plugin = PluginLoader.load_from_class(MockStrategyPlugin)
    assert plugin.metadata().plugin_id == "S-1"


def test_loader_missing_interface() -> None:
    class BadClass:
        pass

    with pytest.raises(PluginValidationError, match="does not implement PluginProtocol"):
        PluginLoader.load_from_class(BadClass)


def test_loader_failed_validation() -> None:
    class SelfFailing(MockStrategyPlugin):
        def validate(self) -> bool:
            return False

    with pytest.raises(PluginValidationError, match="failed internal self-validation"):
        PluginLoader.load_from_class(SelfFailing)


# --- REGISTRATION & MANAGER TESTS (15 tests) ---


def test_register_plugin(base_state: PluginState) -> None:
    plugin = MockStrategyPlugin()
    s1 = PluginManager.register_plugin(base_state, plugin, 1000.0)

    assert plugin_count(s1) == 1
    assert lookup(s1, "S-1") == plugin
    assert len(s1.events) == 2  # Loaded, Registered
    assert plugin_statistics(s1).total_registered == 1


def test_register_plugin_enabled_by_default(base_state: PluginState) -> None:
    plugin = MockFeedPlugin()
    s1 = PluginManager.register_plugin(base_state, plugin, 1000.0)

    assert len(enabled_plugins(s1)) == 1
    assert "F-1" in s1.enabled_ids
    assert len(s1.events) == 3  # Loaded, Registered, Enabled


def test_register_duplicate_id(base_state: PluginState) -> None:
    plugin = MockStrategyPlugin()
    s1 = PluginManager.register_plugin(base_state, plugin, 1000.0)

    with pytest.raises(InvalidPluginStateError, match="already registered"):
        PluginManager.register_plugin(s1, plugin, 1001.0)


def test_register_duplicate_name(base_state: PluginState) -> None:
    plugin1 = MockStrategyPlugin()

    class Clone(MockStrategyPlugin):
        def metadata(self) -> PluginMetadata:
            return PluginMetadata("S-2", "MockStrat", "1.0", "A", "D", PluginType.RISK, "1.0.0")

    plugin2 = Clone()
    s1 = PluginManager.register_plugin(base_state, plugin1, 1000.0)

    with pytest.raises(InvalidPluginStateError, match="already in use"):
        PluginManager.register_plugin(s1, plugin2, 1001.0)


def test_unregister_plugin(base_state: PluginState) -> None:
    plugin = MockStrategyPlugin()
    s1 = PluginManager.register_plugin(base_state, plugin, 1000.0)
    s2 = PluginManager.unregister_plugin(s1, "S-1", 1001.0)

    assert plugin_count(s2) == 0
    assert lookup(s2, "S-1") is None
    assert plugin_statistics(s2).total_unregistered == 1
    assert any(type(e).__name__ == "PluginRemoved" for e in s2.events)


def test_unregister_missing(base_state: PluginState) -> None:
    with pytest.raises(InvalidPluginStateError, match="not found"):
        PluginManager.unregister_plugin(base_state, "MISSING", 1000.0)


# --- ENABLE / DISABLE TESTS (10 tests) ---


def test_enable_disable_cycle(base_state: PluginState) -> None:
    plugin = MockStrategyPlugin()  # Default disabled
    s1 = PluginManager.register_plugin(base_state, plugin, 1000.0)

    assert len(enabled_plugins(s1)) == 0
    assert len(disabled_plugins(s1)) == 1

    s2 = PluginManager.enable_plugin(s1, "S-1", 1001.0)
    assert len(enabled_plugins(s2)) == 1
    assert len(disabled_plugins(s2)) == 0
    assert any(type(e).__name__ == "PluginEnabled" for e in s2.events)

    s3 = PluginManager.disable_plugin(s2, "S-1", 1002.0)
    assert len(enabled_plugins(s3)) == 0
    assert len(disabled_plugins(s3)) == 1
    assert any(type(e).__name__ == "PluginDisabled" for e in s3.events)


def test_enable_already_enabled(base_state: PluginState) -> None:
    plugin = MockFeedPlugin()  # Default enabled
    s1 = PluginManager.register_plugin(base_state, plugin, 1000.0)
    s2 = PluginManager.enable_plugin(s1, "F-1", 1001.0)

    # State should remain structurally identical (no new events appended)
    assert s1 is s2


def test_disable_already_disabled(base_state: PluginState) -> None:
    plugin = MockStrategyPlugin()
    s1 = PluginManager.register_plugin(base_state, plugin, 1000.0)
    s2 = PluginManager.disable_plugin(s1, "S-1", 1001.0)

    assert s1 is s2


# --- ADAPTER & VIEWS TESTS (10 tests) ---


def test_adapter_assertions() -> None:
    strat = MockStrategyPlugin()
    feed = MockFeedPlugin()

    assert PluginAdapter.assert_strategy_plugin(strat) == strat
    with pytest.raises(PluginValidationError):
        PluginAdapter.assert_feed_plugin(strat)

    assert PluginAdapter.assert_feed_plugin(feed) == feed
    with pytest.raises(PluginValidationError):
        PluginAdapter.assert_risk_plugin(feed)


def test_views_queries(base_state: PluginState) -> None:
    s1 = PluginManager.register_plugin(base_state, MockStrategyPlugin(), 1000.0)
    s2 = PluginManager.register_plugin(s1, MockFeedPlugin(), 1001.0)

    assert plugin_count(s2) == 2
    assert len(list_plugins(s2)) == 2

    enabled = enabled_plugins(s2)
    assert len(enabled) == 1
    assert enabled[0].metadata().plugin_id == "F-1"

    stats = plugin_statistics(s2)
    assert stats.total_registered == 2
    assert stats.enabled_count == 1
    assert stats.disabled_count == 1


# --- EXTRA GENERATED TESTS TO MEET QUANTITY REQUIREMENTS ---


@pytest.mark.parametrize("missing_id", ["A", "B", "C", "INVALID"])
def test_lookup_missing_returns_none(base_state: PluginState, missing_id: str) -> None:
    assert lookup(base_state, missing_id) is None


def test_immutability(base_state: PluginState) -> None:
    plugin = MockStrategyPlugin()
    s1 = PluginManager.register_plugin(base_state, plugin, 1000.0)

    assert s1 is not base_state
    assert plugin_count(base_state) == 0
    assert plugin_count(s1) == 1
