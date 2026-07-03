"""High-level orchestration of registry operations and event tracking."""

import uuid
from dataclasses import replace

from alphalab.plugins.events import (
    PluginDisabled,
    PluginEnabled,
    PluginLoaded,
    PluginRegistered,
    PluginRemoved,
)
from alphalab.plugins.protocol import PluginProtocol
from alphalab.plugins.registry import PluginRegistry
from alphalab.plugins.state import PluginState


class PluginManager:
    """Facade orchestrating pure state changes with event generation."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def register_plugin(
        state: PluginState, plugin: PluginProtocol, timestamp: float
    ) -> PluginState:
        """Registers a plugin and emits lifecycle events."""
        s1 = PluginRegistry.register(state, plugin)

        meta = plugin.metadata()
        load_evt = PluginLoaded(PluginManager._create_id(), timestamp, meta.plugin_id)
        reg_evt = PluginRegistered(
            PluginManager._create_id(), timestamp, meta.plugin_id, meta.plugin_type.name
        )

        events = [load_evt, reg_evt]
        if meta.enabled:
            events.append(PluginEnabled(PluginManager._create_id(), timestamp, meta.plugin_id))

        return replace(s1, events=(*s1.events, *events))

    @staticmethod
    def unregister_plugin(state: PluginState, plugin_id: str, timestamp: float) -> PluginState:
        """Removes a plugin and emits the removal event."""
        s1 = PluginRegistry.unregister(state, plugin_id)

        evt = PluginRemoved(PluginManager._create_id(), timestamp, plugin_id)
        return replace(s1, events=(*s1.events, evt))

    @staticmethod
    def enable_plugin(state: PluginState, plugin_id: str, timestamp: float) -> PluginState:
        """Enables an existing plugin."""
        s1 = PluginRegistry.enable(state, plugin_id)
        if s1 is state:
            return state  # No change occurred

        evt = PluginEnabled(PluginManager._create_id(), timestamp, plugin_id)
        return replace(s1, events=(*s1.events, evt))

    @staticmethod
    def disable_plugin(state: PluginState, plugin_id: str, timestamp: float) -> PluginState:
        """Disables an existing plugin."""
        s1 = PluginRegistry.disable(state, plugin_id)
        if s1 is state:
            return state

        evt = PluginDisabled(PluginManager._create_id(), timestamp, plugin_id)
        return replace(s1, events=(*s1.events, evt))
