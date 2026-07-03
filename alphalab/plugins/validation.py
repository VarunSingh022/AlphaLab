"""Validation rules preventing broken extensions or collisions."""

from alphalab.plugins.exceptions import InvalidPluginStateError, PluginValidationError
from alphalab.plugins.protocol import PluginProtocol
from alphalab.plugins.state import PluginState

SUPPORTED_API_VERSIONS = frozenset({"1.0.0", "1.1.0"})


def validate_plugin_metadata(plugin: PluginProtocol) -> None:
    """Ensures a plugin conforms to standard identifying rules."""
    meta = plugin.metadata()

    if not meta.plugin_id.strip():
        raise PluginValidationError("Plugin ID cannot be empty.")

    if not meta.name.strip():
        raise PluginValidationError("Plugin name cannot be empty.")

    if meta.api_version not in SUPPORTED_API_VERSIONS:
        raise PluginValidationError(
            f"Unsupported API version: {meta.api_version}. "
            f"Must be one of {sorted(SUPPORTED_API_VERSIONS)}."
        )


def validate_registration(state: PluginState, plugin: PluginProtocol) -> None:
    """Checks for identity collisions before allowing registration."""
    validate_plugin_metadata(plugin)
    meta = plugin.metadata()

    if meta.plugin_id in state.plugins:
        raise InvalidPluginStateError(f"Plugin ID '{meta.plugin_id}' is already registered.")

    existing_names = {p.metadata().name for p in state.plugins.values()}
    if meta.name in existing_names:
        raise InvalidPluginStateError(f"Plugin Name '{meta.name}' is already in use.")


def validate_lookup(state: PluginState, plugin_id: str) -> None:
    """Verifies a plugin exists in the registry."""
    if plugin_id not in state.plugins:
        raise InvalidPluginStateError(f"Plugin '{plugin_id}' not found.")
