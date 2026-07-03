"""Pure queries exposing transparent Plugin State access."""

from collections.abc import Sequence

from alphalab.plugins.protocol import PluginProtocol
from alphalab.plugins.state import PluginState, PluginStatistics


def plugin_count(state: PluginState) -> int:
    """Returns the total number of registered plugins."""
    return len(state.plugins)


def lookup(state: PluginState, plugin_id: str) -> PluginProtocol | None:
    """Retrieves a specific plugin by ID if it exists."""
    return state.plugins.get(plugin_id)


def list_plugins(state: PluginState) -> Sequence[PluginProtocol]:
    """Returns all registered plugins."""
    return tuple(state.plugins.values())


def enabled_plugins(state: PluginState) -> Sequence[PluginProtocol]:
    """Returns only the plugins that are currently enabled."""
    return tuple(p for k, p in state.plugins.items() if k in state.enabled_ids)


def disabled_plugins(state: PluginState) -> Sequence[PluginProtocol]:
    """Returns only the plugins that are currently disabled."""
    return tuple(p for k, p in state.plugins.items() if k not in state.enabled_ids)


def plugin_statistics(state: PluginState) -> PluginStatistics:
    """Returns tracking metrics for plugin operations."""
    return state.statistics
