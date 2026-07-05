"""Pure functional mutators managing the core plugin map collections."""

from dataclasses import replace

from alphalab.common.registry import with_mapping_item, without_mapping_key
from alphalab.plugins.protocol import PluginProtocol
from alphalab.plugins.state import PluginState
from alphalab.plugins.validation import validate_lookup, validate_registration


class PluginRegistry:
    """Stateless dictionary transformations for the plugin lifecycle."""

    @staticmethod
    def register(state: PluginState, plugin: PluginProtocol) -> PluginState:
        validate_registration(state, plugin)

        meta = plugin.metadata()
        new_plugins = with_mapping_item(state.plugins, meta.plugin_id, plugin)

        new_enabled = set(state.enabled_ids)
        if meta.enabled:
            new_enabled.add(meta.plugin_id)

        stats = replace(
            state.statistics,
            total_registered=state.statistics.total_registered + 1,
            enabled_count=len(new_enabled),
            disabled_count=len(new_plugins) - len(new_enabled),
        )

        return replace(
            state,
            plugins=new_plugins,
            enabled_ids=frozenset(new_enabled),
            statistics=stats,
        )

    @staticmethod
    def unregister(state: PluginState, plugin_id: str) -> PluginState:
        validate_lookup(state, plugin_id)

        new_plugins = without_mapping_key(state.plugins, plugin_id)

        new_enabled = set(state.enabled_ids)
        if plugin_id in new_enabled:
            new_enabled.remove(plugin_id)

        stats = replace(
            state.statistics,
            total_unregistered=state.statistics.total_unregistered + 1,
            enabled_count=len(new_enabled),
            disabled_count=len(new_plugins) - len(new_enabled),
        )

        return replace(
            state,
            plugins=new_plugins,
            enabled_ids=frozenset(new_enabled),
            statistics=stats,
        )

    @staticmethod
    def enable(state: PluginState, plugin_id: str) -> PluginState:
        validate_lookup(state, plugin_id)

        if plugin_id in state.enabled_ids:
            return state

        new_enabled = frozenset(state.enabled_ids | {plugin_id})
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

        new_enabled = frozenset(state.enabled_ids - {plugin_id})
        stats = replace(
            state.statistics,
            enabled_count=len(new_enabled),
            disabled_count=len(state.plugins) - len(new_enabled),
        )
        return replace(state, enabled_ids=new_enabled, statistics=stats)
