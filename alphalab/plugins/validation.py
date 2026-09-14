"""Validation rules preventing broken extensions or collisions."""

from alphalab.common.validators import (
    require_mapping_key,
    require_missing_mapping_key,
    require_non_empty_string,
)
from alphalab.plugins.exceptions import InvalidPluginStateError, PluginValidationError
from alphalab.plugins.protocol import PluginProtocol
from alphalab.plugins.state import PluginState

SUPPORTED_API_VERSIONS = frozenset({"1.0.0", "1.1.0"})


def validate_plugin_metadata(plugin: PluginProtocol) -> None:
    """Ensures a plugin conforms to standard identifying rules."""
    meta = plugin.metadata()

    require_non_empty_string(
        meta.plugin_id,
        "plugin_id",
        message="Plugin ID cannot be empty.",
        exception_type=PluginValidationError,
    )
    require_non_empty_string(
        meta.name,
        "name",
        message="Plugin name cannot be empty.",
        exception_type=PluginValidationError,
    )

    if meta.api_version not in SUPPORTED_API_VERSIONS:
        raise PluginValidationError(
            f"Unsupported API version: {meta.api_version}. "
            f"Must be one of {sorted(SUPPORTED_API_VERSIONS)}."
        )


def validate_registration(state: PluginState, plugin: PluginProtocol) -> None:
    """Checks for identity collisions before allowing registration."""
    validate_plugin_metadata(plugin)
    meta = plugin.metadata()

    require_missing_mapping_key(
        state.plugins,
        meta.plugin_id,
        f"Plugin ID '{meta.plugin_id}' is already registered.",
        exception_type=InvalidPluginStateError,
    )

    # Reads the derived index rather than scanning every registered plugin and
    # calling metadata() on each. That scan was 54% of the registration path at
    # 4,000 plugins, and quadratic -- see alphalab.plugins.state.
    if meta.name in state.registered_names:
        raise InvalidPluginStateError(f"Plugin Name '{meta.name}' is already in use.")


def validate_lookup(state: PluginState, plugin_id: str) -> None:
    """Verifies a plugin exists in the registry."""
    require_mapping_key(
        state.plugins,
        plugin_id,
        f"Plugin '{plugin_id}' not found.",
        exception_type=InvalidPluginStateError,
    )
