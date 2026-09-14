"""Pure functional mutators managing the core plugin map collections."""

from dataclasses import replace

from alphalab.plugins.protocol import PluginProtocol
from alphalab.plugins.state import PluginState
from alphalab.plugins.validation import validate_lookup, validate_registration


class PluginRegistry:
    """Stateless dictionary transformations for the plugin lifecycle."""

    @staticmethod
    def register(state: PluginState, plugin: PluginProtocol) -> PluginState:
        validate_registration(state, plugin)

        meta = plugin.metadata()
        new_plugins = state.plugins.set(meta.plugin_id, plugin)

        new_enabled = state.enabled_ids
        if meta.enabled:
            new_enabled = new_enabled.add(meta.plugin_id)

        stats = replace(
            state.statistics,
            total_registered=state.statistics.total_registered + 1,
            enabled_count=len(new_enabled),
            disabled_count=len(new_plugins) - len(new_enabled),
        )

        return replace(
            state,
            plugins=new_plugins,
            enabled_ids=new_enabled,
            registered_names=state.registered_names.add(meta.name),
            statistics=stats,
        )

    @staticmethod
    def unregister(state: PluginState, plugin_id: str) -> PluginState:
        validate_lookup(state, plugin_id)

        name = state.plugins[plugin_id].metadata().name
        new_plugins = state.plugins.delete(plugin_id)
        new_enabled = state.enabled_ids.discard(plugin_id)

        stats = replace(
            state.statistics,
            total_unregistered=state.statistics.total_unregistered + 1,
            enabled_count=len(new_enabled),
            disabled_count=len(new_plugins) - len(new_enabled),
        )

        return replace(
            state,
            plugins=new_plugins,
            enabled_ids=new_enabled,
            registered_names=state.registered_names.discard(name),
            statistics=stats,
        )

    @staticmethod
    def enable(state: PluginState, plugin_id: str) -> PluginState:
        validate_lookup(state, plugin_id)

        if plugin_id in state.enabled_ids:
            return state

        new_enabled = state.enabled_ids.add(plugin_id)
        stats = replace(
            state.statistics,
            enabled_count=len(new_enabled),
            disabled_count=len(state.plugins) - len(new_enabled),
        )
        return replace(state, enabled_ids=new_enabled, statistics=stats)

    @staticmethod
    def disable(state: PluginState, plugin_id: str) -> PluginState:
        validate_lookup(state, plugin_id)

        if plugin_id not in state.enabled_ids:
            return state

        new_enabled = state.enabled_ids.discard(plugin_id)
        stats = replace(
            state.statistics,
            enabled_count=len(new_enabled),
            disabled_count=len(state.plugins) - len(new_enabled),
        )
        return replace(state, enabled_ids=new_enabled, statistics=stats)
